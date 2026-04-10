"""
Web page routes — calendar, action points, funding, memory, pending changes.

Each page has its HTML builder and related API endpoints.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.auth.auth import has_permission
from app.interfaces.web_auth import get_full_user_from_cookie, get_user_from_cookie
from app.interfaces.web_templates import (
    esc,
    funding_badge,
    mobile_header_html,
    page_head,
    sidebar_html,
)

log = logging.getLogger(__name__)

router = APIRouter()


# ── Pending changes page ─────────────────────────────────────────────────────

@router.get("/pending-changes", response_class=HTMLResponse)
async def pending_changes_page(request: Request):
    user = get_full_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    from app.storage import pending_changes as pc
    changes = pc.list_pending()

    cards: list[str] = []
    for c in changes:
        status = c.get("status", "pending")
        cards.append(
            f'<div class="change-card">'
            f'<h3>{c.get("title", "Untitled")}</h3>'
            f'<div class="change-meta">'
            f'ID: {c.get("id")} &middot; '
            f'Author: {c.get("author", "agent")} &middot; '
            f'Proposed: {str(c.get("proposed_at", ""))[:16]}'
            f'&nbsp; <span class="change-status {status}">{status}</span>'
            f"</div>"
            f"</div>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
{page_head("Changes — CDCN Agent")}
<body>
  <div class="app">
    {sidebar_html("pending", username=user.username, role=user.role)}
    <div class="main-content">
      {mobile_header_html("Changes")}
      <div class="main-topbar">
        <span class="main-topbar-title">Pending Changes</span>
        <button class="icon-btn" id="theme-toggle" onclick="toggleTheme()" title="Toggle theme"></button>
      </div>
      <div class="page-content">
        {"".join(cards) if cards else '<div class="empty-state"><svg width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg><p>No pending changes.</p></div>'}
      </div>
    </div>
  </div>
  <script src="/static/js/sidebar.js"></script>
</body>
</html>"""
    return HTMLResponse(html)


# ── Calendar page ────────────────────────────────────────────────────────────

@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request):
    user = get_full_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    can_edit = has_permission(user.role, "approve_changes") or user.role in ("admin", "staff")

    html = f"""<!DOCTYPE html>
<html lang="en">
{page_head("Calendar — CDCN Agent")}
<body>
  <div class="app">
    {sidebar_html("calendar", username=user.username, role=user.role)}
    <div class="main-content">
      {mobile_header_html("Calendar")}
      <div class="main-topbar">
        <span class="main-topbar-title" id="cal-title">Calendar</span>
        <div style="display:flex;gap:.4rem;align-items:center;">
          <button class="icon-btn" onclick="calNav(-1)" title="Previous month">
            <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="15 18 9 12 15 6"/></svg>
          </button>
          <button class="btn btn-ghost btn-sm" onclick="calToday()">Today</button>
          <button class="icon-btn" onclick="calNav(1)" title="Next month">
            <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg>
          </button>
          {'<button class="btn btn-primary btn-sm" onclick="openAddModal()" style="margin-left:.5rem;">+ Event</button>' if can_edit else ''}
          <button class="icon-btn" id="theme-toggle" onclick="toggleTheme()" title="Toggle theme"></button>
        </div>
      </div>
      <div class="page-content">
        <!-- Upcoming events summary -->
        <div id="upcoming-bar" style="display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:1rem;"></div>
        <!-- Calendar grid -->
        <div id="cal-grid" style="display:grid;grid-template-columns:repeat(7,1fr);gap:6px;"></div>
      </div>
    </div>
  </div>

  <!-- Add/Edit event modal -->
  <div class="modal-backdrop" id="event-modal">
    <div class="modal">
      <h3 id="modal-title">Add Event</h3>
      <form id="event-form" onsubmit="saveEvent(event)">
        <input type="hidden" id="ev-id" value="">
        <div class="form-group">
          <label for="ev-title">Title</label>
          <input type="text" id="ev-title" required>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem;">
          <div class="form-group">
            <label for="ev-date">Date</label>
            <input type="date" id="ev-date" required>
          </div>
          <div class="form-group">
            <label for="ev-time">Time (optional)</label>
            <input type="time" id="ev-time">
          </div>
        </div>
        <div class="form-group">
          <label for="ev-category">Category</label>
          <select id="ev-category" style="width:100%;padding:.6rem .85rem;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-family:inherit;font-size:.9rem;">
            <option value="event">Event</option>
            <option value="meeting">Meeting</option>
            <option value="funding">Funding Deadline</option>
            <option value="statutory">Statutory Deadline</option>
            <option value="policy_review">Policy Review</option>
            <option value="contractual">Contractual</option>
            <option value="other">Other</option>
          </select>
        </div>
        <div class="form-group">
          <label for="ev-notes">Notes</label>
          <input type="text" id="ev-notes" placeholder="Optional details">
        </div>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" onclick="closeModal()">Cancel</button>
          <button type="submit" class="btn btn-primary" id="modal-save-btn">Save</button>
        </div>
      </form>
    </div>
  </div>

  <!-- Event detail popover -->
  <div class="modal-backdrop" id="detail-modal" onclick="this.classList.remove('open')">
    <div class="modal" onclick="event.stopPropagation()" style="max-width:360px;">
      <div id="detail-content"></div>
      <div class="modal-actions" id="detail-actions"></div>
    </div>
  </div>

  <script src="/static/js/sidebar.js"></script>
  <script src="/static/js/calendar.js"></script>
  <script>calInit({'true' if can_edit else 'false'});</script>
</body>
</html>"""
    return HTMLResponse(html)


# ── Calendar API ─────────────────────────────────────────────────────────────

@router.get("/api/calendar/month")
async def calendar_month_api(request: Request, year: int = 0, month: int = 0):
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from app.skills.deadline_tracker import get_calendar_data
    today = datetime.now()
    if year == 0:
        year = today.year
    if month == 0:
        month = today.month
    events = await get_calendar_data(year, month)
    return JSONResponse({"year": year, "month": month, "events": events})


@router.post("/api/calendar/events")
async def calendar_add_event(request: Request):
    user = get_full_user_from_cookie(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not has_permission(user.role, "approve_changes") and user.role not in ("admin", "staff"):
        return JSONResponse({"error": "Permission denied"}, status_code=403)
    from app.skills.deadline_tracker import add_deadline
    body = await request.json()
    event = await add_deadline(
        title=body.get("title", ""),
        category=body.get("category", "event"),
        due_date=body.get("due_date", ""),
        deadline_type=body.get("deadline_type", "reminder"),
        event_time=body.get("event_time", ""),
        notes=body.get("notes", ""),
        assigned_to=body.get("assigned_to", ""),
        created_by=f"web:{user.username}",
    )
    return JSONResponse(event)


@router.put("/api/calendar/events/{event_id}")
async def calendar_update_event(request: Request, event_id: int):
    user = get_full_user_from_cookie(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not has_permission(user.role, "approve_changes") and user.role not in ("admin", "staff"):
        return JSONResponse({"error": "Permission denied"}, status_code=403)
    from app.skills.deadline_tracker import update_deadline
    body = await request.json()
    fields = {k: v for k, v in body.items() if k in ("title", "due_date", "event_time", "category", "notes", "assigned_to", "status") and v is not None}
    result = await update_deadline(event_id, **fields)
    if result is None:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    return JSONResponse(result)


@router.delete("/api/calendar/events/{event_id}")
async def calendar_delete_event(request: Request, event_id: int):
    user = get_full_user_from_cookie(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not has_permission(user.role, "approve_changes") and user.role not in ("admin", "staff"):
        return JSONResponse({"error": "Permission denied"}, status_code=403)
    from app.skills.deadline_tracker import delete_deadline
    ok = await delete_deadline(event_id)
    if not ok:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    return JSONResponse({"deleted": event_id})


@router.post("/api/calendar/events/{event_id}/complete")
async def calendar_complete_event(request: Request, event_id: int):
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from app.skills.deadline_tracker import mark_complete
    ok = await mark_complete(event_id)
    if not ok:
        return JSONResponse({"error": "Event not found"}, status_code=404)
    return JSONResponse({"completed": event_id})


# ── Action Points page ───────────────────────────────────────────────────────

@router.get("/action-points", response_class=HTMLResponse)
async def action_points_page(request: Request):
    user = get_full_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    html = f"""<!DOCTYPE html>
<html lang="en">
{page_head("Action Points — CDCN Agent")}
<body>
  <div class="app">
    {sidebar_html("actions", username=user.username, role=user.role)}
    <div class="main-content">
      {mobile_header_html("Action Points")}
      <div class="main-topbar">
        <span class="main-topbar-title">Action Points</span>
        <button class="icon-btn" id="theme-toggle" onclick="toggleTheme()" title="Toggle theme"></button>
      </div>
      <div class="page-content" style="max-width:720px;">
        <div class="funding-stats" id="ap-stats"></div>
        <div id="ap-list">
          <p style="font-size:.82rem;color:var(--text-muted);">Loading action points...</p>
        </div>
      </div>
    </div>
  </div>
  <script src="/static/js/sidebar.js"></script>
  <script src="/static/js/action-points.js"></script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/api/action-points")
async def action_points_api(request: Request):
    """Return all action points as JSON."""
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from app.skills.action_tracker import list_actions
        items = await list_actions(status=None)
        return JSONResponse({"action_points": items, "count": len(items)})
    except Exception as exc:
        log.warning("action-points API error: %s", exc)
        return JSONResponse({"action_points": [], "count": 0, "error": str(exc)})


@router.post("/api/action-points/{action_id}/status")
async def action_point_status_api(action_id: str, request: Request):
    """Update an action point's status and/or priority."""
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
        from app.skills.action_tracker import update_action
        fields = {}
        if "status" in body:
            fields["status"] = body["status"]
        if "priority" in body:
            fields["priority"] = body["priority"]
        result = await update_action(action_id, **fields)
        if result:
            return JSONResponse({"success": True, "action": result})
        return JSONResponse({"success": False, "error": "Action not found"}, status_code=404)
    except Exception as exc:
        log.warning("action-point status error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


# ── Funding report page ──────────────────────────────────────────────────────

@router.get("/funding", response_class=HTMLResponse)
async def funding_page(request: Request):
    user = get_full_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    html = f"""<!DOCTYPE html>
<html lang="en">
{page_head("Funding — CDCN Agent")}
<body>
  <div class="app">
    {sidebar_html("funding", username=user.username, role=user.role)}
    <div class="main-content">
      {mobile_header_html("Funding")}
      <div class="main-topbar">
        <span class="main-topbar-title">Funding Opportunities</span>
        <button class="icon-btn" id="theme-toggle" onclick="toggleTheme()" title="Toggle theme"></button>
      </div>
      <div class="page-content" style="max-width:860px;">
        <div class="funding-header">
          <div>
            <h3>Funding Landscape</h3>
            <div class="funding-meta" id="funding-meta">Loading&hellip;</div>
          </div>
          <button class="funding-refresh-btn" id="refresh-btn" onclick="refreshFeeds()">
            <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
            Refresh Feeds
          </button>
        </div>
        <div class="funding-stats" id="funding-stats"></div>
        <div id="funding-report">
          <div class="empty-state">
            <svg width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
            <p>Loading funding data&hellip;</p>
          </div>
        </div>
      </div>
    </div>
  </div>
  <script src="/static/js/sidebar.js"></script>
  <script src="/static/js/funding.js"></script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/api/funding/opportunities")
async def funding_opportunities_api(request: Request):
    """Return current funding opportunities as JSON."""
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from app.skills.funding_feed import get_recent_opportunities
        opps = await get_recent_opportunities(n=100, days=30)
        opps = [o for o in opps if o.get("relevance") != "low"]
        return JSONResponse({"opportunities": opps, "count": len(opps)})
    except Exception as exc:
        log.warning("funding API error: %s", exc)
        return JSONResponse({"opportunities": [], "count": 0, "error": str(exc)})


@router.post("/api/funding/refresh")
async def funding_refresh_api(request: Request):
    """Trigger a fresh RSS feed scan and return results."""
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from app.skills.funding_feed import fetch_all_feeds
        result = await fetch_all_feeds()
        result.pop("new_items", None)
        return JSONResponse(result)
    except Exception as exc:
        log.warning("funding refresh error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Memory page ──────────────────────────────────────────────────────────────

@router.get("/memory", response_class=HTMLResponse)
async def memory_page(request: Request):
    user = get_full_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    html = f"""<!DOCTYPE html>
<html lang="en">
{page_head("Memory — CDCN Agent")}
<body>
  <div class="app">
    {sidebar_html("memory", username=user.username, role=user.role)}
    <div class="main-content">
      {mobile_header_html("Memory")}
      <div class="main-topbar">
        <span class="main-topbar-title">Conversation Memory</span>
        <button class="icon-btn" id="theme-toggle" onclick="toggleTheme()" title="Toggle theme"></button>
      </div>
      <div class="page-content" style="max-width:900px;">
        <div style="display:flex;gap:.75rem;align-items:center;margin-bottom:1.25rem;flex-wrap:wrap;">
          <input type="text" id="memory-search" placeholder="Search past conversations&hellip;"
                 style="flex:1;min-width:200px;padding:.5rem .75rem;border:1px solid var(--border);border-radius:6px;background:var(--bg-secondary);color:var(--text);font-size:.85rem;">
          <button id="memory-search-btn" style="padding:.5rem 1rem;border:none;border-radius:6px;background:var(--primary);color:white;cursor:pointer;font-size:.85rem;">Search</button>
        </div>
        <div id="memory-results" style="margin-bottom:1.5rem;"></div>
        <div style="display:flex;align-items:center;gap:.75rem;margin-bottom:.75rem;">
          <h4 style="margin:0;font-size:.9rem;color:var(--text);">Recent Sessions</h4>
          <select id="memory-days" style="padding:.3rem .5rem;border:1px solid var(--border);border-radius:4px;background:var(--bg-secondary);color:var(--text);font-size:.8rem;">
            <option value="3">Last 3 days</option>
            <option value="7" selected>Last 7 days</option>
            <option value="14">Last 14 days</option>
            <option value="30">Last 30 days</option>
          </select>
        </div>
        <div id="memory-sessions"></div>
      </div>
    </div>
  </div>
  <script src="/static/js/sidebar.js"></script>
  <script src="/static/js/memory.js"></script>
  <style>
    .memory-table {{ width:100%; border-collapse:collapse; font-size:.82rem; }}
    .memory-table th {{ text-align:left; padding:.5rem; border-bottom:2px solid var(--border); color:var(--text-muted); font-weight:600; font-size:.75rem; text-transform:uppercase; }}
    .memory-table td {{ padding:.45rem .5rem; border-bottom:1px solid var(--border); color:var(--text); }}
    .memory-table tr:hover {{ background:var(--bg-secondary); }}
    .memory-view-btn {{ padding:.2rem .6rem; border:1px solid var(--border); border-radius:4px; background:var(--bg-secondary); color:var(--primary); cursor:pointer; font-size:.75rem; }}
    .memory-view-btn:hover {{ background:var(--primary); color:white; }}
    .memory-transcript {{ max-height:500px; overflow-y:auto; border:1px solid var(--border); border-radius:8px; padding:.75rem; background:var(--bg-secondary); }}
    .memory-msg-user {{ margin-bottom:.6rem; padding:.4rem .6rem; background:var(--bg-tertiary, var(--bg)); border-radius:6px; font-size:.82rem; color:var(--text); }}
    .memory-msg-assistant {{ margin-bottom:.6rem; padding:.4rem .6rem; border-left:3px solid var(--primary); font-size:.82rem; color:var(--text); }}
    .memory-search-result {{ margin-bottom:.75rem; padding:.6rem; border:1px solid var(--border); border-radius:6px; background:var(--bg-secondary); }}
    .memory-result-meta {{ font-size:.72rem; color:var(--text-subtle); margin-bottom:.3rem; }}
    .memory-result-excerpt {{ font-size:.82rem; color:var(--text); }}
  </style>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/api/memory/sessions")
async def memory_sessions_api(request: Request, days: int = 7):
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)

    from app.skills.conversation_memory import ConversationMemorySkill
    skill = ConversationMemorySkill()
    result = await skill.run(action="list", days=min(days, 90))
    return JSONResponse({
        "sessions": result.metadata.get("sessions", []) if result.metadata else [],
    })


@router.get("/api/memory/session")
async def memory_session_api(request: Request, session_id: str = "", date: str = ""):
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    if not session_id:
        return JSONResponse({"detail": "session_id required"}, status_code=400)

    from app.skills.conversation_memory import ConversationMemorySkill
    skill = ConversationMemorySkill()
    result = await skill.run(action="read", session_id=session_id, date=date)
    if not result.success:
        return JSONResponse({"detail": result.error}, status_code=404)
    return JSONResponse({
        "messages": result.metadata.get("messages", []) if result.metadata else [],
        "session_id": result.metadata.get("session_id", "") if result.metadata else "",
        "date": result.metadata.get("date", "") if result.metadata else "",
    })


@router.get("/api/memory/search")
async def memory_search_api(request: Request, q: str = ""):
    user = get_user_from_cookie(request)
    if not user:
        return JSONResponse({"detail": "Unauthorised"}, status_code=401)
    if not q.strip():
        return JSONResponse({"detail": "Search query required"}, status_code=400)

    from app.skills.conversation_memory import ConversationMemorySkill
    skill = ConversationMemorySkill()
    result = await skill.run(action="search", query=q, limit=30)
    return JSONResponse({
        "matches": result.metadata.get("matches", []) if result.metadata else [],
        "query": q,
    })


def register_page_routes(target_router: APIRouter) -> None:
    """Mount all page routes onto the given router."""
    target_router.include_router(router)

    # Also mount admin and dashboard routes
    from app.interfaces.web_admin import router as admin_router
    from app.interfaces.web_dashboard import router as dashboard_router
    target_router.include_router(admin_router)
    target_router.include_router(dashboard_router)
