# CDCN Agent — API Reference

## Authentication

All API endpoints (except `/login`, `/register`, `/health`) require authentication.

**Cookie auth (web UI):** Login via `POST /login` sets an httponly `access_token` cookie containing a JWT.

**Bearer auth (API clients):** Pass `Authorization: Bearer <jwt_token>` header.

JWT tokens expire after 8 hours. Tokens contain:

```json
{"sub": "username", "role": "admin|staff|trustee", "exp": timestamp}
```

## Rate Limiting

- 20 requests per user per minute (token bucket)
- Login: 5 failed attempts triggers 15-minute lockout

---

## Health

### GET /health

No auth required.

**Response:**

```json
{"status": "ok", "mode": "wake", "started_at": "2026-04-08T17:46:41+00:00"}
```

---

## Authentication Endpoints

### GET /login

Returns HTML login form.

### POST /login

Authenticate and receive JWT cookie.

**Form fields:** `username` (string), `password` (string)

**Success:** 303 redirect to `/` with `access_token` cookie set

**Failure:** 401 with error message in HTML

### GET /logout

Clears auth cookie, redirects to `/login`.

### GET /register

Returns HTML registration form.

### POST /register

Submit registration request for admin approval.

**Form fields:** `display_name`, `email`, `username`, `password`, `confirm_password`

**Success:** Redirect to login with success message

**Failure:** 400 (validation) or 409 (duplicate username)

### GET /forgot-password

Returns informational page directing users to contact admin.

---

## Chat API

### POST /api/chat

Send a message and receive streaming response.

**Auth:** Bearer token required

**Request body:**

```json
{
  "message": "What was discussed at the last board meeting?",
  "channel_id": "optional-channel-id"
}
```

**Response:** Server-Sent Events (SSE) stream of text tokens.

### WebSocket /ws

Real-time chat via WebSocket.

**Auth:** Cookie auth (`access_token` cookie)

**Protocol:**

Client sends:

```json
{"message": "Hello", "thread_id": "optional-thread-id"}
```

Server sends (in sequence):

```json
{"type": "thinking"}
{"type": "token", "content": "chunk of text"}
{"type": "token", "content": "more text..."}
{"type": "done"}
```

Error:

```json
{"type": "error", "detail": "description"}
```

Thread-scoped messages include `thread_id` in all payloads.

---

## Gateway API (prefix: /api)

### GET /api/status

Agent status and state information.

**Auth:** Bearer token

**Response:**

```json
{"mode": "wake", "skills": [...], "sessions": 5}
```

### GET /api/sessions

List active sessions.

**Auth:** Bearer token (admin only)

### GET /api/sessions/{session_id}

Get specific session details.

**Auth:** Bearer token (admin only)

---

## Thread API

### GET /api/threads

List threads for the current user.

**Auth:** Cookie

**Response:**

```json
[{"id": "abc123", "name": "Budget Discussion", "created_by": "alice", "created_at": "..."}]
```

### POST /api/threads

Create a new thread.

**Auth:** Cookie

**Request body:**

```json
{"name": "Thread title", "participants": ["alice", "bob"], "type": "group"}
```

### GET /api/threads/{thread_id}

Get thread details.

### GET /api/threads/{thread_id}/messages?limit=200

Get thread messages.

**Query params:** `limit` (int, default 200)

### POST /api/threads/{thread_id}/participants

Add a participant to a thread.

**Request body:**

```json
{"username": "charlie"}
```

### DELETE /api/threads/{thread_id}/participants/{username}

Remove a participant from a thread.

---

## Presence & History

### POST /api/heartbeat

Update user's online presence. Call every 60 seconds.

**Auth:** Cookie

### GET /api/online

Get list of currently online users.

**Auth:** Cookie

**Response:**

```json
{"users": ["alice", "bob"]}
```

### GET /api/history

Get chat history for the current user.

**Auth:** Cookie

### GET /api/users/search?q=alice

Search users by username.

**Auth:** Cookie

**Query params:** `q` (string)

---

## Archive API

### GET /archive

HTML file explorer page.

### GET /api/archive/ls?path=

List directory contents.

**Auth:** Cookie

**Query params:** `path` (string, relative to archive root)

**Response:**

```json
{"items": [{"name": "minutes", "type": "dir", "size": 0}, {"name": "policy.pdf", "type": "file", "size": 123456}]}
```

### GET /api/archive/download?path=file.pdf

Download a file from the archive.

**Auth:** Cookie

**Query params:** `path` (string, relative to archive root)

### POST /api/archive/upload

Upload files to the archive.

**Auth:** Cookie (requires `index` permission -- staff/admin)

**Content-Type:** `multipart/form-data`

**Fields:** `files` (file[]), `path` (string, destination directory)

**Limits:** 10 MB per file, allowed types: PDF, DOCX, TXT, MD, XLSX, CSV, ODT, PPTX, JPG, PNG, GIF

**Validation:** Extension whitelist + magic-byte content check

### POST /api/archive/mkdir

Create a folder in the archive.

**Auth:** Cookie (requires `index` permission)

**Request body:**

```json
{"path": "parent/directory", "name": "new-folder"}
```

### DELETE /api/archive/item?path=folder/file.pdf

Delete a file or folder.

**Auth:** Cookie (requires `delete_documents` permission -- admin only)

---

## Calendar API

### GET /api/calendar/month?year=2026&month=4

Get calendar events for a month.

### POST /api/calendar/events

Create a calendar event.

**Request body:**

```json
{"title": "Board Meeting", "date": "2026-04-15", "time": "14:00", "description": "Quarterly review"}
```

### PUT /api/calendar/events/{event_id}

Update a calendar event.

### DELETE /api/calendar/events/{event_id}

Delete a calendar event.

### POST /api/calendar/events/{event_id}/complete

Mark an event as completed.

---

## Action Points API

### GET /api/action-points

List action points.

**Auth:** Cookie

### POST /api/action-points/{action_id}/status

Update action point status.

**Request body:**

```json
{"status": "completed", "notes": "Done on 2026-04-08"}
```

---

## Funding API

### GET /api/funding/opportunities

List funding opportunities.

### POST /api/funding/refresh

Trigger a refresh of funding feeds.

**Auth:** Cookie (staff/admin)

---

## Memory API

### GET /api/memory/sessions

List memory sessions.

### GET /api/memory/session?session_id=abc

Get a specific memory session.

### GET /api/memory/search?q=solar

Search agent memory.

---

## Admin API

### GET /admin

Admin dashboard HTML page.

**Auth:** Cookie (admin only)

### GET /admin/dashboard

Dashboard with statistics.

### GET /api/admin/dashboard/stats

Dashboard statistics JSON.

### POST /api/admin/users

Create a new user.

**Request body:**

```json
{"username": "newuser", "password": "secureP@ss1", "role": "staff"}
```

### POST /api/admin/users/{username}/edit

Update user details (display name, email).

### POST /api/admin/users/{username}/role

Change user role.

**Request body:**

```json
{"role": "admin"}
```

### POST /api/admin/users/{username}/suspend

Suspend a user account.

### POST /api/admin/users/{username}/reactivate

Reactivate a suspended user.

### POST /api/admin/registrations/{reg_id}/approve

Approve a registration request.

### POST /api/admin/registrations/{reg_id}/reject

Reject a registration request.

### POST /api/admin/roles

Create a custom role.

**Request body:**

```json
{"name": "observer", "permissions": ["search", "query"], "description": "Read-only observer"}
```

### POST /api/admin/roles/{role_name}

Update a custom role.

### DELETE /api/admin/roles/{role_name}

Delete a custom role (cannot delete system roles or roles with assigned users).

---

## Permissions Reference

| Permission | Description | Roles |
|------------|-------------|-------|
| search | Search documents | All |
| query | Query the agent | All |
| read_pending | View pending changes | All |
| individual_chat | Individual chat | All |
| group_chat | Group threads | All |
| index | Upload/index documents | Staff, Admin |
| write_skill | Create skills | Staff, Admin |
| add_users | Manage users | Admin |
| delete_documents | Delete from archive | Admin |
| approve_changes | Approve config changes | Admin |
| view_audit_log | View audit log | Admin |
| trigger_heartbeat | Trigger system heartbeat | Admin |
