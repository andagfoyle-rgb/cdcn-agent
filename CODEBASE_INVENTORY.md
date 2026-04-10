# CODEBASE INVENTORY — CDCN Agent

Generated: 2026-04-08
Purpose: Reference document for restructuring. If any feature goes missing, check this inventory.

---

## 1. FILE STRUCTURE (pre-restructure)

```
app/
├── __init__.py
├── main.py                          (245 lines) — FastAPI app, lifespan, middleware
├── config.py                        (133 lines) — Pydantic Settings
├── unified_config.py                (103 lines) — Config validation/logging
├── llm_client.py                    (340 lines) — LLM client (Ollama + OpenAI-compat)
├── state_manager.py                 (443 lines) — Wake/dream lifecycle
├── cli.py                           (416 lines) — Click CLI commands
├── auth/
│   ├── __init__.py
│   └── auth.py                      (734 lines) — JWT, RBAC, user CRUD, rate limiting
├── gateway/
│   ├── __init__.py
│   ├── router.py                    (1370 lines) — AgentRouter, agentic loop
│   ├── session.py                   (351 lines) — Session management
│   └── identity_map.py              (88 lines)  — Cross-platform identity linking
├── interfaces/
│   ├── __init__.py
│   ├── base_adapter.py              (85 lines)  — Abstract adapter base
│   ├── web.py                       (5427 lines) — ALL web: routes, WS, CSS, JS, HTML
│   ├── discord_adapter.py           (~150 lines) — Discord bot
│   ├── telegram_bot.py              (~150 lines) — Telegram bot
│   └── email_adapter.py             (312 lines) — SMTP email
├── retrieval/
│   ├── __init__.py
│   └── pipeline.py                  (284 lines) — Three-layer retrieval
├── skills/
│   ├── __init__.py
│   ├── base.py                      (28 lines)  — SkillResult, BaseSkill ABC
│   ├── action_tracker.py            (423 lines) — Action point CRUD
│   ├── board_pack.py                (497 lines) — Board meeting pack generation
│   ├── calendar_manager.py          (~200 lines) — Calendar/event management
│   ├── chunker.py                   (551 lines) — Document chunking
│   ├── clarify.py                   (~100 lines) — Clarification handling
│   ├── conversation_memory.py       (~200 lines) — Conversation history search
│   ├── cronjob_manager.py           (378 lines) — Cron job orchestration
│   ├── deadline_tracker.py          (514 lines) — Deadline/calendar CRUD
│   ├── delegate.py                  (~100 lines) — Skill delegation
│   ├── document_editor.py           (438 lines) — Document read/save/list
│   ├── document_index.py            (~150 lines) — Index management
│   ├── document_parser.py           (538 lines) — Document parsing
│   ├── docx_converter.py            (639 lines) — Markdown → DOCX
│   ├── dream_worker.py              (1119 lines) — Overnight task runner
│   ├── funding_feed.py              (1062 lines) — RSS funding scraper
│   ├── funding_tracker.py           (447 lines) — Funding deadline tracking
│   ├── indexer.py                   (1022 lines) — Document scan/chunk/upsert
│   ├── meeting_prep.py              (~200 lines) — Meeting preparation
│   ├── memory.py                    (572 lines) — Layered memory system
│   ├── notify.py                    (~100 lines) — Notifications
│   ├── retriever.py                 (475 lines) — Retrieval wrapper
│   ├── scheduler.py                 (1458 lines) — APScheduler task scheduling
│   ├── search.py                    (971 lines) — Three-layer document search
│   ├── skill_builder.py             (~200 lines) — Skill drafting
│   ├── vision.py                    (~100 lines) — OCR/vision
│   └── writer.py                    (~200 lines) — Document drafting
├── storage/
│   ├── __init__.py
│   ├── audit_log.py                 (717 lines) — SQLite audit tables
│   ├── file_store.py                (35 lines)  — JSON key-value store
│   ├── pending_changes.py           (457 lines) — Pending change lifecycle
│   ├── threads.py                   (224 lines) — Thread/message CRUD
│   └── vector_store.py              (352 lines) — ChromaDB wrapper
├── utils/
│   ├── __init__.py
│   ├── backup.py                    (~100 lines) — Backup utilities
│   ├── dates.py                     (105 lines) — Date extraction
│   ├── db_pool.py                   (115 lines) — SQLite connection pool
│   ├── extraction.py                (45 lines)  — Attendee extraction
│   ├── file_ops.py                  (54 lines)  — Safe file I/O
│   └── schema.py                    (97 lines)  — DB migration helpers
└── static/
    ├── marked.min.js                — Markdown renderer
    ├── rubik.css                    — Font stylesheet
    └── *.ttf                        — Font files
```

---

## 2. EVERY ROUTE/ENDPOINT

### Web UI Pages (cookie-auth, HTMLResponse)
| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| GET | `/` | `chat_ui` | Main chat page with WebSocket, threads, sidebar |
| GET | `/login` | `login_form` | Login form |
| POST | `/login` | `login_submit` | Authenticate, set JWT cookie |
| GET | `/logout` | `logout` | Clear cookie, redirect |
| GET | `/register` | `register_form` | Registration form |
| POST | `/register` | `register_submit` | Submit registration request |
| GET | `/forgot-password` | `forgot_password_form` | Password reset info page |
| GET | `/archive` | `archive_page` | Document file explorer |
| GET | `/calendar` | `calendar_page` | Calendar with grid view |
| GET | `/action-points` | `action_points_page` | Action points dashboard |
| GET | `/funding` | `funding_page` | Funding opportunities report |
| GET | `/memory` | `memory_page` | Conversation memory browser |
| GET | `/pending-changes` | `pending_changes_page` | Pending agent changes |
| GET | `/admin` | `admin_page` | User/role administration |
| GET | `/admin/dashboard` | `admin_dashboard` | Token usage dashboard (Chart.js) |

### Static Assets (served from web.py)
| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| GET | `/chat.css` | `serve_css` | Main stylesheet (inline `_CSS`) |
| GET | `/chat.js` | `serve_js` | Chat JavaScript (inline `_JS`) |

### WebSocket
| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| WS | `/ws` | `websocket_endpoint` | Chat WebSocket (cookie-auth) |

### API Endpoints (cookie-auth, JSON)
| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| POST | `/api/heartbeat` | `heartbeat` | Touch user presence |
| GET | `/api/online` | `online_users` | List online users |
| GET | `/api/history` | `chat_history` | Last 48h chat messages |
| GET | `/api/users/search` | `users_search` | Username autocomplete |
| GET | `/api/threads` | `api_list_threads` | List user's threads |
| POST | `/api/threads` | `api_create_thread` | Create thread |
| GET | `/api/threads/{id}` | `api_get_thread` | Get thread details |
| GET | `/api/threads/{id}/messages` | `api_thread_messages` | Get thread messages |
| POST | `/api/threads/{id}/participants` | `api_add_participant` | Add participant |
| DELETE | `/api/threads/{id}/participants/{u}` | `api_remove_participant` | Remove participant |
| GET | `/api/archive/ls` | `archive_ls` | List directory |
| GET | `/api/archive/download` | `archive_download` | Download file |
| POST | `/api/archive/upload` | `archive_upload` | Upload files |
| POST | `/api/archive/mkdir` | `archive_mkdir` | Create folder |
| DELETE | `/api/archive/item` | `archive_delete` | Delete file/folder |
| GET | `/api/calendar/month` | `calendar_month_api` | Get month events |
| POST | `/api/calendar/events` | `calendar_add_event` | Add event |
| PUT | `/api/calendar/events/{id}` | `calendar_update_event` | Update event |
| DELETE | `/api/calendar/events/{id}` | `calendar_delete_event` | Delete event |
| POST | `/api/calendar/events/{id}/complete` | `calendar_complete_event` | Mark complete |
| GET | `/api/action-points` | `action_points_api` | List action points |
| POST | `/api/action-points/{id}/status` | `action_point_status_api` | Update AP status |
| GET | `/api/funding/opportunities` | `funding_opportunities_api` | List opportunities |
| POST | `/api/funding/refresh` | `funding_refresh_api` | Trigger RSS scan |
| GET | `/api/memory/sessions` | `memory_sessions_api` | List memory sessions |
| GET | `/api/memory/session` | `memory_session_api` | Get session messages |
| GET | `/api/memory/search` | `memory_search_api` | Search conversations |
| POST | `/api/admin/users` | `admin_create_user` | Create user |
| POST | `/api/admin/users/{u}/edit` | `admin_edit_user` | Edit user info |
| POST | `/api/admin/users/{u}/role` | `admin_set_role` | Change user role |
| POST | `/api/admin/users/{u}/suspend` | `admin_suspend` | Suspend user |
| POST | `/api/admin/users/{u}/reactivate` | `admin_reactivate` | Reactivate user |
| POST | `/api/admin/registrations/{id}/approve` | `admin_approve_reg` | Approve registration |
| POST | `/api/admin/registrations/{id}/reject` | `admin_reject_reg` | Reject registration |
| POST | `/api/admin/roles` | `admin_create_role` | Create role |
| POST | `/api/admin/roles/{name}` | `admin_update_role` | Update role perms |
| DELETE | `/api/admin/roles/{name}` | `admin_delete_role` | Delete role |
| GET | `/api/admin/dashboard/stats` | `api_dashboard_stats` | Token usage stats |

### Gateway API (mounted at /api prefix via gateway_router)
| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| POST | `/api/chat` | gateway chat | Main chat endpoint |
| GET | `/api/status` | gateway status | Agent status/mode |

### FastAPI App (main.py)
| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| GET | `/health` | `health` | Health check |

---

## 3. UI COMPONENTS IN web.py

### CSS Sections (~923 lines, `_CSS` variable)
- Reset & accessibility (skip-link, sr-only, focus-visible)
- Light mode variables (:root)
- Dark mode variables ([data-theme="dark"])
- Login page styles
- App shell (grid layout)
- Sidebar (nav, logo, footer, user profile)
- Status indicator (wake/dream dots, animations)
- Mobile header & hamburger
- Desktop top bar
- Main content area
- Thread list (sidebar panel)
- Autocomplete dropdown
- Participant chips
- Messages (agent, user, other-user rows)
- Code blocks (always dark theme)
- Message timestamps, date separators
- Thinking indicator (bouncing dots)
- Floating input dock
- Legacy input area
- Cards
- Page layout (archive, other pages)
- Breadcrumb
- File explorer (grid, items, icons)
- Upload zone (drag & drop)
- Modals
- Alerts
- Change cards (pending changes)
- Online users panel
- Mini calendar
- Action point cards (with urgency, deferred, completed states)
- Funding report cards & badges
- Responsive breakpoints (mobile < 768px)

### JavaScript Sections
- `_SIDEBAR_JS` (~85 lines): theme toggle, sidebar open/close, status polling, heartbeat, online users polling
- `_JS` (chat.js, ~750 lines): WebSocket client, thread management, message rendering, markdown rendering, autocomplete, new thread modal, manage participants modal, keyboard accessibility
- `_FUNDING_JS` (~120 lines): funding card rendering, stats, report grouping, refresh feeds
- `_ACTION_POINTS_JS` (~130 lines): action point rendering, status updates, expand/collapse
- `_MEMORY_JS` (~80 lines): session list, session viewer, search
- Calendar JS (~240 lines inline): calendar grid rendering, event modals, add/edit/delete/complete
- Archive JS (~110 lines inline): folder creation, delete, upload with progress bar
- Admin JS (~110 lines inline): user CRUD, role management, registration approval/rejection
- Dashboard JS (~280 lines inline): Chart.js rendering (timeline, users, skills), summary cards, user table sorting

### HTML Template Functions
- `_mini_calendar_html()` — sidebar mini calendar
- `_sidebar_html(active, username, role)` — full sidebar with nav, status, online users, calendar
- `_page_head(title, extra_style)` — shared <head> block
- `_mobile_header_html(title)` — mobile hamburger header
- `_build_archive_page(path, user_role, username, flash, flash_type)` — archive file explorer
- `_funding_badge(relevance)` — funding relevance badge
- `_render_funding_card(opp)` — funding opportunity card
- `_role_badge(role)` — admin role badge
- `_esc(s)` — HTML escape helper

### Static HTML Templates
- `_LOGIN_HTML` — login page
- `_REGISTER_HTML` — registration page
- `_FORGOT_HTML` — forgot password page

---

## 4. HELPER FUNCTIONS IN web.py

- `_touch_presence(username)` — update online presence
- `_get_online_users()` → list[str] — get users online in last 90s
- `_validate_upload_mime(content, filename)` → bool — magic byte validation
- `_get_user_from_cookie(request_or_ws)` → str|None — JWT cookie → username
- `_get_full_user_from_cookie(request_or_ws)` → User|None — JWT cookie → User object
- `_archive_root()` → Path — resolved archive path
- `_safe_archive_path(user_path)` → Path|None — path traversal protection
- `_fmt_size(n)` → str — human-readable file size
- `_file_icon(name)` → str — SVG icon by extension
- `_broadcast_to_thread(thread_id, payload, exclude_user)` — WS broadcast to thread participants
- `_detect_document_refinement(original_path, new_content, username)` — diff uploaded vs existing doc

### Constants
- `_ALLOWED_EXTS` — permitted upload extensions
- `_MAX_UPLOAD_BYTES` — 10 MB upload limit
- `_MAX_MESSAGE_LENGTH` — 10,000 char message limit
- `_ALLOWED_MIMES` — accepted MIME types
- `_active_ws` — dict of active WebSocket connections
- `_online_sessions` — dict of username → last_seen timestamps
- `_PRESENCE_TTL` — 90 seconds online timeout
- `_FOLDER_ICON` — SVG folder icon
- `_ROLE_COLOURS` — admin/staff/trustee badge colours

---

## 5. CLASSES

### WebAdapter (web.py:5392)
- Extends `BaseAdapter`
- `start()` — no-op (routes registered via APIRouter)
- `stop()` — close all WebSocket connections
- `send_message(destination, text)` — send via WebSocket
- `send_typing(channel_id)` — no-op

### AgentRouter (gateway/router.py)
- Orchestrates: guard → session → prompt → LLM → skill → response → persist
- `handle_message()` — full agentic turn, yields tokens
- Prompt injection detection
- Skill call extraction (JSON fence + XML fallback)
- Response cleaning
- Citation verification
- Memory consolidation (every 10 exchanges)

### CDCNLLMClient (llm_client.py)
- Supports Ollama and OpenAI-compatible APIs
- `chat()`, `chat_stream()`, `chat_with_tools()`, `embed()`
- Token estimation, message trimming
- Retry with exponential backoff
- Audit logging of all calls

### AgentStateManager (state_manager.py)
- Wake/dream lifecycle
- Wake-on-LAN support
- LLM endpoint swapping
- Morning orientation, pending changes application
- Sleep/wake notifications

### SessionManager (gateway/session.py)
- JSON-backed session persistence
- Message history with RAG context stripping
- Multi-user recent message aggregation

---

## 6. SKILLS REGISTRY

| Skill Key | Class | File | Purpose |
|-----------|-------|------|---------|
| search | SearchSkill | skills/search.py | Three-layer document search |
| indexer | IndexerSkill | skills/indexer.py | Document scan/chunk/upsert |
| writer | WriterSkill | skills/writer.py | LLM document drafting |
| dream_worker | DreamWorkerSkill | skills/dream_worker.py | Overnight background tasks |
| skill_builder | SkillBuilderSkill | skills/skill_builder.py | Draft new skills |
| memory | MemorySkill | skills/memory.py | System prompt, journal, memory |
| funding_feed | FundingFeedSkill | skills/funding_feed.py | RSS funding scraper |
| document_editor | DocumentEditorSkill | skills/document_editor.py | Document read/save/list |
| calendar_manager | CalendarManagerSkill | skills/calendar_manager.py | Calendar management |

### Other skill files (not registered in main.py but used internally):
- `action_tracker.py` — Action point CRUD (used by web API)
- `board_pack.py` — Board meeting pack generation
- `chunker.py` — Document chunking logic
- `clarify.py` — Clarification handling
- `conversation_memory.py` — Conversation history (used by memory page API)
- `cronjob_manager.py` — Cron job orchestration
- `deadline_tracker.py` — Deadline/calendar data (used by calendar API)
- `delegate.py` — Skill delegation/routing
- `document_index.py` — Index management
- `document_parser.py` — Document parsing (unstructured)
- `docx_converter.py` — Markdown → DOCX conversion
- `funding_tracker.py` — Funding deadline tracking
- `meeting_prep.py` — Meeting preparation
- `notify.py` — Notifications
- `retriever.py` — Retrieval wrapper
- `scheduler.py` — APScheduler task scheduling
- `vision.py` — OCR/vision

---

## 7. DATABASE TABLES (SQLite)

### audit_log.db
- `audit_log` — catch-all event log (id, ts, actor, action, target, detail JSON)
- `llm_calls` — LLM API call tracking (prompt/completion tokens, latency)
- `document_ops` — document indexing operations
- `auth_events` — login/token events
- `state_transitions` — wake/dream transitions
- `dream_cycles` — completed dream runs
- `pending_change_actions` — skill config changes
- `funding_opportunities` — scraped funding entries
- `page_hashes` — content hash tracking for dedup
- `learned_skills` — auto-discovered patterns
- `shared_messages` — broadcast messages

### auth database (within auth.py)
- `users` — username, hashed_password, role, active, display_name, email, created_at
- `registration_requests` — pending registrations
- `roles` — custom roles with permissions

### threads database (threads.py)
- `threads` — id, name, created_by, created_at
- `thread_participants` — thread_id, username, added_at, added_by
- `thread_messages` — id, thread_id, sender, role, content, ts

---

## 8. EXTERNAL INTEGRATIONS

- **Ollama** — local LLM inference (configurable model)
- **SiliconFlow/GLM-5** — OpenAI-compatible cloud LLM
- **ChromaDB** — vector store for document embeddings
- **Discord** — bot via discord.py
- **Telegram** — bot via python-telegram-bot
- **SMTP** — outbound email notifications
- **Chart.js** — dashboard visualizations (CDN)
- **Tailwind CSS** — utility classes (CDN)
- **marked.min.js** — Markdown rendering (local static)
- **APScheduler** — task scheduling

---

## 9. CONFIGURATION

- `.env` file loaded via Pydantic Settings
- Key settings: ollama_base_url, ollama_model, discord/telegram tokens, secret_key, storage paths, wake/sleep schedule, rate limits, SMTP config
- Security: JWT (HS256), httponly cookies, CSP headers, CORS whitelist, path traversal protection, rate limiting, prompt injection detection
