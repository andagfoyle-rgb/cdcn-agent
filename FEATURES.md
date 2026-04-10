# CDCN Agent — Feature Registry

Comprehensive inventory of all features, skills, scheduled jobs, API endpoints, integrations, and infrastructure components.

---

## 1. Web Interface Features

All pages require authentication (cookie-based JWT). Unauthenticated requests redirect to `/login`.

| Page | Route | Description |
|------|-------|-------------|
| Chat | `/` | Real-time WebSocket chat with the agent. Thread-based conversations with support for individual and group threads. Markdown rendering, participant management, and typing indicators. |
| Login | `/login` | Username/password authentication form. Sets `access_token` cookie (8-hour TTL). |
| Register | `/register` | Self-service registration. Submissions require admin approval before the account becomes active. |
| Forgot Password | `/forgot-password` | Password reset request form. |
| Logout | `/logout` | Clears the session cookie and redirects to login. |
| Archive | `/archive` | File explorer for the CDCN document archive. Browse folders, upload files (drag-and-drop), create folders, download and delete items. Supports PDF, DOCX, TXT, MD, XLSX, CSV, ODT, PPTX, and image files (max 50 MB). Detects document refinements on re-upload. |
| Calendar | `/calendar` | Interactive monthly calendar displaying events, meetings, funding deadlines, statutory deadlines, policy reviews, and contractual dates. Supports add, edit, delete, and mark-complete actions (role-gated). |
| Action Points | `/action-points` | List and manage action points from CDCN meetings. Status and priority updates via inline controls. |
| Funding | `/funding` | Funding opportunities dashboard showing relevant UK/Scottish funding feeds. Manual refresh trigger and relevance filtering. |
| Memory | `/memory` | Browse and search past conversation history across all interfaces. Lists recent sessions by date range (3–30 days) with full transcript viewing and keyword search. |
| Admin | `/admin` | User administration panel (admin-only). Create users, approve/reject registrations, edit user profiles, reset passwords, suspend/reactivate accounts, assign roles. Full RBAC role and permission editor. |
| Dashboard | `/admin/dashboard` | Token usage analytics with Chart.js charts (admin-only). Tracks LLM calls, prompt/completion tokens, cost estimates, per-user breakdown, skill usage, auth events, document operations, and dream cycles. Configurable time periods (24h to all-time). |
| Pending Changes | `/pending-changes` | View agent-proposed changes awaiting human approval. |

---

## 2. Skills

All skills extend `BaseSkill` (defined in `app/skills/base.py`) and implement an async `run(**kwargs)` method returning a `SkillResult`.

| Skill Name | Class | File | Description |
|------------|-------|------|-------------|
| `search` | `DocumentSearchSkill` | `app/skills/search.py` | Search the CDCN document archive using keyword and semantic search. |
| `indexer` | `DocumentIndexerSkill` | `app/skills/indexer.py` | Index new or modified documents (PDF, DOCX, TXT, Markdown) from the archive into the vector store. |
| `writer` | `DocumentWriterSkill` | `app/skills/writer.py` | Draft a CDCN document from a template. Generates DOCX output with download links. |
| `memory` | `MemorySkill` | `app/skills/memory.py` | Manage CDCN's organisational memory: read context, update long-term memory, write journal and session logs, check upcoming funding deadlines. |
| `dream_worker` | `DreamWorkerSkill` | `app/skills/dream_worker.py` | Six overnight consolidation tasks run during DREAM mode (memory consolidation, journal writing, etc.). |
| `skill_builder` | `SkillBuilderSkill` | `app/skills/skill_builder.py` | Draft a new CDCN Agent skill from a plain-English description. |
| `funding_feed` | `FundingFeedSkill` | `app/skills/funding_feed.py` | Monitor RSS feeds from UK/Scottish funders and surface relevant funding opportunities. |
| `document_editor` | `DocumentEditorSkill` | `app/skills/document_editor.py` | Open, read, edit, and save documents in the CDCN archive. |
| `calendar_manager` | `CalendarManagerSkill` | `app/skills/calendar_manager.py` | Manage the CDCN calendar — add events and meetings, view upcoming dates. |
| `action_tracker` | `ActionTrackerSkill` | `app/skills/action_tracker.py` | Track action points from CDCN meetings. Supports list, add, update, and complete operations. |
| `deadline_tracker` | `DeadlineTrackerSkill` | `app/skills/deadline_tracker.py` | Track CDCN obligations and deadlines. Supports list, add, update, complete, and delete operations. |
| `funding_tracker` | `FundingTrackerSkill` | `app/skills/funding_tracker.py` | Track funding applications from draft to outcome. |
| `board_pack` | `BoardPackSkill` | `app/skills/board_pack.py` | Generate a complete board pack combining agenda, minutes, actions, and reports. |
| `meeting_prep` | `MeetingPrepSkill` | `app/skills/meeting_prep.py` | Generate a meeting preparation pack for a CDCN board meeting. |
| `conversation_memory` | `ConversationMemorySkill` | `app/skills/conversation_memory.py` | Search and browse past conversation history across all interfaces. |
| `notify` | `NotifySkill` | `app/skills/notify.py` | Send a notification to Discord, Telegram, and/or Email. |
| `vision` | `VisionSkill` | `app/skills/vision.py` | Analyse an image using the vision model. Can describe content and extract information. |
| `clarify` | `ClarifySkill` | `app/skills/clarify.py` | Ask the user a clarifying question when more information is needed. |
| `delegate` | `DelegateSkill` | `app/skills/delegate.py` | Run multiple skills in parallel to handle complex, multi-step tasks. |
| `cronjob_manager` | `CronjobManagerSkill` | `app/skills/cronjob_manager.py` | List, add, remove, pause, or resume scheduled jobs. |
| `scheduler` | `SchedulerSkill` | `app/skills/scheduler.py` | APScheduler-based recurring task runner (thin wrapper exposing start/stop API). |

### Supporting Modules (not skills, but used by skills)

| Module | File | Purpose |
|--------|------|---------|
| Document Parser | `app/skills/document_parser.py` | Extract text and metadata from PDF, DOCX, TXT, and Markdown files. |
| DOCX Converter | `app/skills/docx_converter.py` | Convert markdown/text content to formatted DOCX documents. |
| Funding Feed DB | `app/skills/funding_feed_db.py` | SQLite persistence layer for funding feed data. |
| Funding Feed Parser | `app/skills/funding_feed_parser.py` | RSS/Atom feed parsing for funding sources. |
| Indexer DB | `app/skills/indexer_db.py` | SQLite persistence for the document index. |
| Indexer Metadata | `app/skills/indexer_metadata.py` | Document metadata extraction for the indexer. |
| Indexer Parsers | `app/skills/indexer_parsers.py` | File-type-specific parsers for the indexer. |
| Retriever | `app/skills/retriever.py` | Vector similarity retrieval for RAG context. |
| Search Keyword | `app/skills/search_keyword.py` | Keyword-based search implementation. |
| Chunker | `app/skills/chunker.py` | Text chunking for vector store ingestion. |
| Dream Helpers | `app/skills/dream_helpers.py` | Utility functions for dream-mode tasks. |
| Dream Tasks | `app/skills/dream_tasks.py` | Core dream-mode task implementations. |
| Dream Tasks Extended | `app/skills/dream_tasks_ext.py` | Additional dream-mode tasks. |
| Scheduler Helpers | `app/skills/scheduler_helpers.py` | Helper functions and retry decorator for scheduled jobs. |
| Scheduler Jobs | `app/skills/scheduler_jobs.py` | Primary scheduled job implementations (wake, sleep, journal, funding, indexing, overdue). |
| Scheduler Jobs Extended | `app/skills/scheduler_jobs_extended.py` | Extended jobs (heartbeat, weekly digest, monthly governance, session archive). |
| Scheduler Maintenance | `app/skills/scheduler_maintenance.py` | Infrastructure maintenance jobs (backup, DB maintenance, disk monitor, backup verify). |

---

## 3. Scheduled Jobs

All jobs are registered via APScheduler in `app/skills/scheduler.py`. Times are UTC.

| Job ID | Schedule | Source | Description | Wake-gated |
|--------|----------|--------|-------------|------------|
| `wake` | Cron at `WAKE_START_TIME` | `scheduler_jobs.py` | Transition agent from DREAM to WAKE mode | No |
| `sleep` | Cron at `WAKE_END_TIME` | `scheduler_jobs.py` | Transition agent from WAKE to DREAM mode | No |
| `heartbeat` | Interval (configurable hours) | `scheduler_jobs_extended.py` | Periodic heartbeat check during wake hours | Yes |
| `auto_index` | Every 6 hours | `scheduler_jobs.py` | Re-index new/modified documents in the archive | Yes |
| `journal` | Cron at `JOURNAL_TIME` | `scheduler_jobs.py` | Write daily journal entry | Yes |
| `weekly_digest` | Monday 07:15 | `scheduler_jobs_extended.py` | Generate weekly activity digest | Yes |
| `monthly_governance` | 1st of month 08:00 | `scheduler_jobs_extended.py` | Monthly governance compliance check | Yes |
| `overdue_check` | Daily 08:30 | `scheduler_jobs.py` | Check for overdue deadlines and action points | Yes |
| `funding_feed_am` | Daily 07:30 | `scheduler_jobs.py` | Morning funding feed RSS scan | Yes |
| `funding_feed_noon` | Daily 12:00 | `scheduler_jobs.py` | Midday funding feed RSS scan | Yes |
| `session_archive` | Daily 03:00 | `scheduler_jobs_extended.py` | Archive old conversation sessions | No |
| `nightly_backup` | Daily 04:00 | `scheduler_maintenance.py` | Full nightly backup of databases and config | No |
| `backup_verify` | Daily 05:00 | `scheduler_maintenance.py` | Verify integrity of latest backup | No |
| `disk_monitor` | Daily 08:35 | `scheduler_maintenance.py` | Monitor disk space usage and alert if low | No |
| `db_maintenance` | Sunday 02:00 | `scheduler_maintenance.py` | Weekly database VACUUM and integrity checks | No |

---

## 4. API Endpoints

### Gateway API (prefix: `/api`)

Mounted from `app/gateway/router.py`. Requires Bearer token authentication.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat` | Stream a response from the agentic loop (injection guard, session, skill detection, LLM, persist). |
| GET | `/api/status` | Return agent state (wake/dream), uptime, and mode info. |
| GET | `/api/sessions` | List today's chat sessions. |
| GET | `/api/sessions/{session_id}` | Return a specific session by ID. |

### Web Routes (no prefix)

Mounted from `app/interfaces/web_routes.py`.

#### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/login` | Login page |
| POST | `/login` | Authenticate and set session cookie |
| GET | `/logout` | Clear session and redirect |
| GET | `/register` | Registration page |
| POST | `/register` | Submit registration (requires admin approval) |
| GET | `/forgot-password` | Password reset form |

#### Presence & Chat History

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/heartbeat` | Touch user presence (keep-alive) |
| GET | `/api/online` | List currently online users |
| GET | `/api/history` | Return last 48 hours of chat messages |
| GET | `/api/users/search` | Username autocomplete (prefix match, max 10) |

#### Threads

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/threads` | List threads for the current user |
| POST | `/api/threads` | Create a new thread (individual or group) |
| GET | `/api/threads/{id}` | Get thread details and participants |
| GET | `/api/threads/{id}/messages` | Get thread messages (default limit: 200) |
| POST | `/api/threads/{id}/participants` | Add a participant to a thread |
| DELETE | `/api/threads/{id}/participants/{username}` | Remove a participant from a thread |

#### Archive

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/archive/ls` | List directory contents as JSON |
| GET | `/api/archive/download` | Download a file from the archive |
| POST | `/api/archive/upload` | Upload files (multipart, with MIME validation) |
| POST | `/api/archive/mkdir` | Create a new folder |
| DELETE | `/api/archive/item` | Delete a file or folder |

#### Calendar

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/calendar/month` | Get events for a given month |
| POST | `/api/calendar/events` | Add a new calendar event |
| PUT | `/api/calendar/events/{id}` | Update an existing event |
| DELETE | `/api/calendar/events/{id}` | Delete an event |
| POST | `/api/calendar/events/{id}/complete` | Mark an event as complete |

#### Action Points

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/action-points` | List all action points |
| POST | `/api/action-points/{id}/status` | Update action point status/priority |

#### Funding

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/funding/opportunities` | List current funding opportunities (last 30 days, excludes low relevance) |
| POST | `/api/funding/refresh` | Trigger a fresh RSS feed scan |

#### Memory

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/memory/sessions` | List recent conversation sessions (configurable days) |
| GET | `/api/memory/session` | Read a specific session's transcript |
| GET | `/api/memory/search` | Search past conversations by keyword |

#### Admin — User Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/admin/users` | Create a new user |
| POST | `/api/admin/users/{username}/edit` | Edit user display name, email, or password |
| POST | `/api/admin/users/{username}/role` | Change a user's role |
| POST | `/api/admin/users/{username}/suspend` | Suspend a user account |
| POST | `/api/admin/users/{username}/reactivate` | Reactivate a suspended account |
| POST | `/api/admin/registrations/{id}/approve` | Approve a pending registration |
| POST | `/api/admin/registrations/{id}/reject` | Reject a pending registration |

#### Admin — Roles

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/admin/roles` | Create a new role |
| POST | `/api/admin/roles/{name}` | Update a role's permissions and description |
| DELETE | `/api/admin/roles/{name}` | Delete a custom role |

#### Admin — Dashboard

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/admin/dashboard/stats` | Aggregated token usage, cost, skill breakdown, auth events, document ops, dream cycles |

#### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check returning `{status: "ok"}` plus agent state |

#### WebSocket

| Protocol | Endpoint | Description |
|----------|----------|-------------|
| WS | `/ws` | Real-time chat messaging, thread routing, and agent response streaming |

---

## 5. Integrations

| Integration | Adapter File | Description |
|-------------|-------------|-------------|
| **Discord Bot** | `app/interfaces/discord_adapter.py` | discord.py v2.3+ bot. Maps Discord server roles to CDCN internal roles via `DISCORD_ROLE_MAPPING` env var. Routes free-text messages through the agentic loop. Splits long responses at paragraph boundaries (max 1990 chars per message). Supports identity linking between Discord and web accounts. |
| **Telegram Bot** | `app/interfaces/telegram_bot.py` | python-telegram-bot v20+ adapter. Allowlist-based access control via `TELEGRAM_ALLOWED_USER_IDS`. All allowlisted users receive the "staff" role. Splits responses at paragraph boundaries (max 4000 chars). |
| **WebSocket Chat** | `app/interfaces/web_socket.py` | FastAPI WebSocket endpoint at `/ws`. Handles real-time chat messaging, thread-scoped conversations, and agent response streaming. Broadcasts messages to all online thread participants. |
| **Email Adapter** | `app/interfaces/email_adapter.py` | Email interface adapter (used by the NotifySkill for sending notifications). |

All adapters extend `BaseAdapter` (`app/interfaces/base_adapter.py`) and are started concurrently during app lifespan. Each adapter receives the `AgentRouter` singleton for message handling.

---

## 6. Infrastructure

### Agentic Loop (`app/gateway/`)

| Component | File | Purpose |
|-----------|------|---------|
| AgentRouter | `router.py` | Orchestrates the full agentic turn: injection guard, session load, system prompt, RAG context, LLM call, skill detection/execution, response streaming, session persist, and memory consolidation (every 10 exchanges). |
| Prompt Builder | `prompt_builder.py` | System prompt assembly with 60-second TTL cache. Builds tool definitions from registered skills. |
| Tool Handler | `tool_handler.py` | Prompt injection detection, skill call parsing (OpenAI function-call, GLM-5 XML, and Ollama regex formats), skill execution, and response cleaning. |
| Retrieval | `retrieval.py` | RAG context injection, prefetch cache lookup, writer skill intercept, and memory consolidation. |
| Session Manager | `session.py` | Session creation, persistence, and retrieval. Stores conversation history per user per day. |
| Identity Map | `identity_map.py` | Cross-platform identity linking (e.g., Discord user to web account). |

### Storage (`app/storage/`)

| Component | File | Purpose |
|-----------|------|---------|
| Vector Store | `vector_store.py` | Embedding-based vector store for semantic search and RAG retrieval. |
| Audit Log | `audit_log.py` | SQLite-based audit logging of LLM calls, auth events, document operations, dream cycles, and learned skills. |
| Pending Changes | `pending_changes.py` | Tracks agent-proposed changes awaiting human approval. |
| Threads | `threads.py` | SQLite-backed thread and message storage for multi-user chat. |
| File Store | `file_store.py` | File system abstraction for the document archive. |

### Auth (`app/auth/`)

Role-based access control (RBAC) with configurable roles and permissions. Supports user creation, registration approval workflow, rate limiting (20 requests/minute), and JWT token authentication.

### State Management

| Component | File | Purpose |
|-----------|------|---------|
| AgentStateManager | `app/state_manager.py` | Two-state wake/dream lifecycle. Manages transitions between WAKE (full capability) and DREAM (low-power consolidation) modes. Handles Wake-on-LAN sequencing, LLM endpoint swap, adapter enable/disable, morning orientation, and overnight summary broadcast. |

### Memory System (`MemorySkill`)

Layered memory architecture:
- **Long-term identity/rules** (`skills_config/memory/`): `soul.md`, `agents.md`, `heartbeat.md`, `style_guide.md`, `knowledge_graph.md`, `memory.md`
- **Runtime data** (`data/memory/`): daily session logs, journal entries, dream-mode journals, current task pointer, prefetch cache, heartbeat log

### Security

- Security headers middleware (CSP, HSTS, X-Frame-Options, X-XSS-Protection, Referrer-Policy, Permissions-Policy)
- CORS restricted to configured origins
- Cookie-based JWT auth with HttpOnly, SameSite=Lax
- Prompt injection detection guard
- File upload MIME validation and blocked executable types
- Path traversal protection on archive operations

### Configuration

- `app/config.py` — Settings loaded from environment / `.env` file
- `app/unified_config.py` — Unified configuration management
- `app/llm_client.py` — LLM client supporting both Ollama and OpenAI-compatible APIs (SiliconFlow GLM-5V-Turbo)

---

*Last updated: 2026-04-08*
