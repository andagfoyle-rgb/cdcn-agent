"""
Web route handlers — login/register, chat UI, presence/heartbeat APIs, thread APIs.

Archive, calendar, action points, funding, memory, admin, and dashboard
pages are in separate modules imported via mount_all_routes().
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.auth.auth import (
    authenticate_user,
    create_access_token,
    has_permission,
    list_users,
    register_user,
)
from app.config import settings
from app.interfaces.web_auth import (
    get_full_user_from_cookie,
    get_online_users,
    get_user_from_cookie,
    touch_presence,
)
from app.interfaces.web_templates import (
    esc,
    forgot_html,
    login_html,
    mobile_header_html,
    page_head,
    register_html,
    sidebar_html,
)
from app.interfaces.web_socket import websocket_endpoint

log = logging.getLogger(__name__)

router = APIRouter()


# ── Static CSS (now served from file) ─────────────────────────────────────────

@router.get("/chat.css")
async def serve_css():
    """Serve main stylesheet from static file."""
    css_path = Path(__file__).parent.parent / "static" / "css" / "main.css"
    try:
        content = css_path.read_text()
    except FileNotFoundError:
        content = "/* CSS file not found */"
    return Response(content=content, media_type="text/css; charset=utf-8")


@router.get("/chat.js")
async def serve_js():
    """Serve chat JavaScript — combines sidebar + chat modules."""
    js_dir = Path(__file__).parent.parent / "static" / "js"
    parts = []
    for name in ("sidebar.js", "chat.js"):
        try:
            parts.append((js_dir / name).read_text())
        except FileNotFoundError:
            parts.append(f"/* {name} not found */")
    return Response(content="\n".join(parts), media_type="application/javascript; charset=utf-8")


# ── Login / Logout / Register ─────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_form():
    return HTMLResponse(login_html().replace("<!-- ERROR_PLACEHOLDER -->", ""))


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    user = authenticate_user(username, password)
    if not user:
        return HTMLResponse(
            login_html().replace("<!-- ERROR_PLACEHOLDER -->", '<p class="error-msg">Invalid credentials. Please try again.</p>'),
            status_code=401,
        )
    token = create_access_token(user)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=3600 * 8,
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_form():
    return HTMLResponse(register_html().replace("<!-- REGISTER_PLACEHOLDER -->", ""))


@router.post("/register")
async def register_submit(request: Request):
    form = await request.form()
    display_name = str(form.get("display_name", "")).strip()
    email = str(form.get("email", "")).strip()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm_password", ""))

    if not username or not password or not display_name or not email:
        return HTMLResponse(
            register_html().replace("<!-- REGISTER_PLACEHOLDER -->", '<p class="error-msg">All fields are required.</p>'),
            status_code=400,
        )
    if len(password) < 8:
        return HTMLResponse(
            register_html().replace("<!-- REGISTER_PLACEHOLDER -->", '<p class="error-msg">Password must be at least 8 characters.</p>'),
            status_code=400,
        )
    if password != confirm:
        return HTMLResponse(
            register_html().replace("<!-- REGISTER_PLACEHOLDER -->", '<p class="error-msg">Passwords do not match.</p>'),
            status_code=400,
        )

    try:
        register_user(username, password, display_name, email)
    except ValueError as exc:
        return HTMLResponse(
            register_html().replace("<!-- REGISTER_PLACEHOLDER -->", f'<p class="error-msg">{esc(str(exc))}</p>'),
            status_code=409,
        )

    return HTMLResponse(
        login_html().replace("<!-- ERROR_PLACEHOLDER -->", '<p class="success-msg">Registration submitted! An administrator will review your request.</p>'),
    )


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_form():
    return HTMLResponse(forgot_html())


# ── Presence / online users API ────────────────────────────────────────────────

@router.post("/api/heartbeat")
async def heartbeat(request: Request):
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    touch_presence(user)
    return JSONResponse({"ok": True})


@router.get("/api/online")
async def online_users(request: Request):
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    touch_presence(user)
    return JSONResponse({"users": [{"username": u} for u in get_online_users()]})


@router.get("/api/history")
async def chat_history(request: Request):
    """Return the last 48 hours of visible chat messages for all users."""
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    from app.gateway.session import SessionManager
    mgr = SessionManager()
    messages = mgr.load_all_recent_messages(hours=48)
    return JSONResponse({"messages": messages, "current_user": user})


# ── Thread API ─────────────────────────────────────────────────────────────────

@router.get("/api/users/search")
async def users_search(request: Request, q: str = Query("", min_length=0)):
    """Username autocomplete — prefix match on active users, max 10."""
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    query = q.strip().lower()
    if not query:
        return JSONResponse({"users": []})
    all_users = list_users()
    matches = [
        u["username"]
        for u in all_users
        if u.get("active", 1) and u["username"].lower().startswith(query)
    ]
    return JSONResponse({"users": matches[:10]})


@router.get("/api/threads")
async def api_list_threads(request: Request):
    user_obj = get_full_user_from_cookie(request)
    if not user_obj:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    from app.storage.threads import list_user_threads
    threads = list_user_threads(user_obj.username)
    can_ind = has_permission(user_obj.role, "individual_chat")
    can_grp = has_permission(user_obj.role, "group_chat")
    if not can_ind or not can_grp:
        threads = [
            t for t in threads
            if (t.get("participant_count", 1) > 1 and can_grp)
            or (t.get("participant_count", 1) <= 1 and can_ind)
        ]
    return JSONResponse({"threads": threads})


@router.post("/api/threads")
async def api_create_thread(request: Request):
    user_obj = get_full_user_from_cookie(request)
    if not user_obj:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    body = await request.json()
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse({"detail": "Thread name is required"}, status_code=400)
    participants = body.get("participants", [])
    if not isinstance(participants, list):
        participants = []
    others = [p for p in participants if p != user_obj.username]
    is_group = len(others) > 0
    if is_group and not has_permission(user_obj.role, "group_chat"):
        return JSONResponse({"detail": "You do not have permission to create group threads."}, status_code=403)
    if not is_group and not has_permission(user_obj.role, "individual_chat"):
        return JSONResponse({"detail": "You do not have permission to create individual threads."}, status_code=403)
    from app.storage.threads import create_thread
    thread_id = create_thread(name=name, created_by=user_obj.username, participants=participants)
    return JSONResponse({"id": thread_id, "name": name}, status_code=201)


@router.get("/api/threads/{thread_id}")
async def api_get_thread(request: Request, thread_id: str):
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    from app.storage.threads import get_thread, get_participants, is_participant
    if not is_participant(thread_id, user):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    thread = get_thread(thread_id)
    if not thread:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    thread["participants"] = get_participants(thread_id)
    return JSONResponse(thread)


@router.get("/api/threads/{thread_id}/messages")
async def api_thread_messages(request: Request, thread_id: str, limit: int = Query(200)):
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    from app.storage.threads import is_participant, get_messages
    if not is_participant(thread_id, user):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return JSONResponse({"messages": get_messages(thread_id, limit=limit)})


@router.post("/api/threads/{thread_id}/participants")
async def api_add_participant(request: Request, thread_id: str):
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    from app.storage.threads import is_participant, add_participant
    if not is_participant(thread_id, user):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    body = await request.json()
    username = str(body.get("username", "")).strip()
    if not username:
        return JSONResponse({"detail": "username is required"}, status_code=400)
    target = [u for u in list_users() if u["username"] == username and u.get("active", 1)]
    if not target:
        return JSONResponse({"detail": "User not found"}, status_code=404)
    added = add_participant(thread_id, username, added_by=user)
    return JSONResponse({"added": added, "username": username})


@router.delete("/api/threads/{thread_id}/participants/{username}")
async def api_remove_participant(request: Request, thread_id: str, username: str):
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    from app.storage.threads import is_participant, remove_participant
    if not is_participant(thread_id, user):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    removed = remove_participant(thread_id, username)
    if not removed:
        return JSONResponse({"detail": "Cannot remove thread creator"}, status_code=400)
    return JSONResponse({"removed": True, "username": username})


# ── Chat UI ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def chat_ui(request: Request):
    user = get_full_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    send_icon = '<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>'
    can_individual = "1" if has_permission(user.role, "individual_chat") else "0"
    can_group = "1" if has_permission(user.role, "group_chat") else "0"
    html = f"""<!DOCTYPE html>
<html lang="en">
{page_head("CDCN Agent")}
<body>
  <a href="#input" class="skip-link">Skip to chat input</a>
  <div id="chat-perms" data-can-individual="{can_individual}" data-can-group="{can_group}" data-username="{user.username}" style="display:none;"></div>
  <div class="app">
    {sidebar_html("chat", username=user.username, role=user.role)}
    <div class="main-content">
      {mobile_header_html("CDCN Agent")}
      <div class="main-topbar">
        <span class="main-topbar-title" id="topbar-title">Chat</span>
        <div style="display:flex;gap:.35rem;align-items:center;">
          <button class="icon-btn" id="manage-participants-btn" onclick="openParticipantsModal()" title="Manage participants" aria-label="Manage participants" style="display:none;">
            <svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
          </button>
          <button class="icon-btn" id="theme-toggle" onclick="toggleTheme()" title="Toggle theme" aria-label="Toggle theme"></button>
        </div>
      </div>
      <div class="chat-layout">
        <div class="thread-list" id="thread-list">
          <div class="thread-list-header">
            <span>Threads</span>
            <button class="icon-btn" id="new-chat-btn" onclick="openNewThreadModal()" title="New thread" aria-label="New thread" style="width:24px;height:24px;">
              <svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            </button>
          </div>
          <div class="thread-list-body" id="thread-list-body" role="listbox" aria-label="Conversation threads"></div>
        </div>
        <main class="chat-area" role="main">
          <div id="messages" class="messages" aria-label="Chat messages" aria-live="polite">
            <div class="chat-body" id="chat-body" role="log" aria-relevant="additions"></div>
          </div>
          <div class="input-dock">
            <div class="input-float">
              <label for="input" class="sr-only">Message input</label>
              <textarea
                id="input"
                rows="1"
                placeholder="Message CDCN Agent\u2026"
                disabled
                aria-label="Type a message"
              ></textarea>
              <button id="send" class="send-btn" disabled aria-label="Send message">{send_icon}</button>
            </div>
            <div class="input-hint" aria-hidden="true">Enter to send &nbsp;&middot;&nbsp; Shift+Enter for new line</div>
          </div>
        </main>
      </div>
    </div>
  </div>
  <!-- New Thread Modal -->
  <div class="modal-backdrop" id="new-thread-modal">
    <div class="modal" style="max-width:440px;">
      <h3>New Thread</h3>
      <div class="form-group">
        <label class="form-label" for="new-thread-name">Thread name</label>
        <input class="form-input" id="new-thread-name" type="text" placeholder="e.g. Funding strategy" autocomplete="off">
      </div>
      <div class="form-group">
        <label class="form-label">Participants</label>
        <div class="chip-list" id="new-thread-chips"></div>
        <div class="ac-wrap">
          <input class="form-input" id="new-thread-user-input" type="text" placeholder="Type a username\u2026" autocomplete="off">
          <div class="ac-dropdown" id="new-thread-ac"></div>
        </div>
      </div>
      <div class="modal-actions">
        <button class="btn btn-secondary" onclick="closeNewThreadModal()">Cancel</button>
        <button class="btn btn-primary" id="create-thread-btn" onclick="createThread()">Create</button>
      </div>
    </div>
  </div>
  <!-- Manage Participants Modal -->
  <div class="modal-backdrop" id="participants-modal">
    <div class="modal" style="max-width:440px;">
      <h3>Manage Participants</h3>
      <div class="form-group">
        <label class="form-label">Current participants</label>
        <div class="chip-list" id="participants-chips"></div>
      </div>
      <div class="form-group">
        <label class="form-label">Add participant</label>
        <div class="ac-wrap">
          <input class="form-input" id="participants-user-input" type="text" placeholder="Type a username\u2026" autocomplete="off">
          <div class="ac-dropdown" id="participants-ac"></div>
        </div>
      </div>
      <div class="modal-actions">
        <button class="btn btn-secondary" onclick="closeParticipantsModal()">Done</button>
      </div>
    </div>
  </div>
  <script src="/static/marked.min.js"></script>
  <script src="/chat.js"></script>
</body>
</html>"""
    return HTMLResponse(html)


# ── WebSocket ──────────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket_endpoint(websocket)


# ── Static file serving ──────────────────────────────────────────────────────

@router.get("/static/{file_type}/{filename}")
async def serve_static(file_type: str, filename: str):
    """Serve static files from app/static/."""
    static_dir = Path(__file__).parent.parent / "static" / file_type
    target = (static_dir / filename).resolve()
    if not str(target).startswith(str(static_dir.resolve())):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    if not target.exists():
        return JSONResponse({"detail": "Not found"}, status_code=404)
    mime_map = {".css": "text/css", ".js": "application/javascript"}
    mime = mime_map.get(target.suffix, "application/octet-stream")
    return Response(content=target.read_text(), media_type=f"{mime}; charset=utf-8")


def mount_all_routes(target_router: APIRouter) -> None:
    """Mount all route handlers onto the given router."""
    target_router.include_router(router)

    from app.interfaces.web_archive import router as archive_router
    target_router.include_router(archive_router)

    from app.interfaces.web_pages import register_page_routes
    register_page_routes(target_router)
