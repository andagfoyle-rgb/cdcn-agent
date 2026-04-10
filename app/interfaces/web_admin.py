"""
Web admin page and API — user management, roles, registrations.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.auth.auth import (
    ALL_PERMISSIONS,
    approve_registration,
    create_role,
    create_user,
    delete_role,
    get_pending_registrations,
    has_permission,
    list_roles,
    list_users,
    reactivate_user,
    reject_registration,
    reset_user_password,
    suspend_user,
    update_role,
    update_user_info,
    update_user_role,
)
from app.interfaces.web_auth import get_full_user_from_cookie
from app.interfaces.web_templates import (
    esc,
    mobile_header_html,
    page_head,
    role_badge,
    sidebar_html,
)

log = logging.getLogger(__name__)

router = APIRouter()


# ── Admin page ───────────────────────────────────────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user = get_full_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not has_permission(user.role, "add_users"):
        return HTMLResponse("<p>Access denied.</p>", status_code=403)

    users = list_users()
    pending = get_pending_registrations()
    roles = list_roles()
    role_names = [r["name"] for r in roles]

    # ── User rows ────────────────────────────────────────────────────
    user_rows = []
    for u in users:
        uname = esc(u["username"])
        dname = esc(u.get("display_name") or "")
        email = esc(u.get("email") or "")
        status_dot = ('<span style="color:#22c55e;font-size:.8rem;">●</span>'
                      if u.get("active") else
                      '<span style="color:#ef4444;font-size:.8rem;">●</span>')
        active = bool(u.get("active", 1))
        toggle_btn = (
            f'<button class="btn btn-sm btn-ghost" '
            f'onclick="adminAction(\'suspend\',\'{uname}\')" '
            f'style="color:var(--danger-fg);">Suspend</button>'
            if active else
            f'<button class="btn btn-sm btn-ghost" '
            f'onclick="adminAction(\'reactivate\',\'{uname}\')">Reactivate</button>'
        )
        role_opts = "".join(
            f'<option value="{r}" {"selected" if r == u.get("role") else ""}>{r}</option>'
            for r in role_names
        )
        user_rows.append(f"""
        <tr class="admin-row" id="row-{uname}">
          <td class="atd">{status_dot} <strong>{uname}</strong></td>
          <td class="atd">{role_badge(u.get('role','staff'))}</td>
          <td class="atd">
            <div class="user-detail" id="detail-{uname}">
              <span class="detail-display">{dname or '<em style="opacity:.4">—</em>'}</span>
            </div>
          </td>
          <td class="atd">
            <div class="user-detail">
              <span class="detail-display">{email or '<em style="opacity:.4">—</em>'}</span>
            </div>
          </td>
          <td class="atd" style="font-size:.8rem;color:var(--text-muted);">{str(u.get('created_at',''))[:10]}</td>
          <td class="atd">
            <div style="display:flex;align-items:center;gap:.35rem;flex-wrap:wrap;">
              <button class="btn btn-sm btn-ghost" onclick="openEditUser('{uname}','{dname}','{email}')">Edit</button>
              <select class="role-select" data-user="{uname}" style="font-size:.78rem;padding:.15rem .3rem;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-family:inherit;cursor:pointer;">
                {role_opts}
              </select>
              <button class="btn btn-sm btn-ghost" onclick="changeRole('{uname}')">Set</button>
              {toggle_btn}
            </div>
          </td>
        </tr>""")

    # ── Pending registration rows ─────────────────────────────────────
    pending_rows = []
    for p in pending:
        pending_rows.append(f"""
        <div class="change-card" id="reg-{p['id']}">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap;">
            <div>
              <div style="font-weight:600;color:var(--text);">{esc(p['username'])}
                <span style="font-size:.7rem;color:var(--text-muted);font-weight:400;margin-left:.4rem;">
                  requested {role_badge(p.get('requested_role','staff'))}
                </span>
              </div>
              <div class="change-meta">{esc(p.get('display_name',''))}
                {'&nbsp;&middot;&nbsp;' + esc(p['email']) if p.get('email') else ''}</div>
              {('<div style="font-size:.8rem;color:var(--text-muted);margin-top:.2rem;">'
                + esc(p.get('reason','')) + '</div>') if p.get('reason') else ''}
            </div>
            <div style="display:flex;gap:.5rem;flex-shrink:0;">
              <button class="btn btn-sm btn-primary"
                onclick="approveReg({p['id']})">Approve</button>
              <button class="btn btn-sm btn-danger"
                onclick="rejectReg({p['id']})">Reject</button>
            </div>
          </div>
        </div>""")

    pending_section = ("".join(pending_rows)
                       if pending_rows else
                       '<p style="color:var(--text-muted);font-size:.875rem;">No pending registrations.</p>')

    # ── Roles section ─────────────────────────────────────────────────
    all_perms_list = list(ALL_PERMISSIONS)
    role_cards = []
    for r in roles:
        rname = esc(r["name"])
        rdesc = esc(r.get("description") or "")
        is_sys = r.get("is_system", False)
        perm_checks = ""
        for perm in all_perms_list:
            checked = "checked" if perm in r["permissions"] else ""
            perm_checks += (
                f'<label style="display:inline-flex;align-items:center;gap:.3rem;font-size:.78rem;'
                f'color:var(--text);cursor:pointer;white-space:nowrap;">'
                f'<input type="checkbox" class="role-perm-cb" data-role="{rname}" '
                f'value="{perm}" {checked}> {perm}</label> '
            )
        delete_btn = (
            f'<button class="btn btn-sm btn-ghost" style="color:var(--danger-fg);" '
            f'onclick="deleteRole(\'{rname}\')">Delete</button>'
            if not is_sys else ""
        )
        sys_badge = (' <span style="font-size:.65rem;color:var(--text-muted);font-weight:400;">(system)</span>'
                     if is_sys else "")
        role_cards.append(f"""
        <div class="change-card" id="role-{rname}">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:.5rem;margin-bottom:.5rem;flex-wrap:wrap;">
            <div style="font-weight:600;color:var(--text);">{rname}{sys_badge}</div>
            <div style="display:flex;gap:.4rem;">
              <button class="btn btn-sm btn-ghost" onclick="saveRole('{rname}')">Save</button>
              {delete_btn}
            </div>
          </div>
          <div style="margin-bottom:.4rem;">
            <input type="text" class="role-desc-input" data-role="{rname}" value="{rdesc}"
              placeholder="Description" style="width:100%;padding:.3rem .5rem;font-size:.8rem;
              border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);
              color:var(--text);font-family:inherit;">
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:.4rem .8rem;">
            {perm_checks}
          </div>
        </div>""")

    create_role_opts = "".join(
        f'<option value="{r}">{r}</option>' for r in role_names
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
{page_head("Admin — CDCN Agent", '''
    .admin-row {{ border-bottom: 1px solid var(--border); }}
    .admin-row:last-child {{ border-bottom: none; }}
    .admin-row:hover {{ background: var(--surface-2); }}
    .admin-table {{ width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }}
    .admin-table th {{ padding: .55rem 1rem; text-align: left; font-size: .72rem; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; color: var(--text-muted); background: var(--surface-2); border-bottom: 1px solid var(--border); }}
    .atd {{ padding: .55rem 1rem; font-size: .85rem; color: var(--text); }}
    .admin-section-title {{ font-size: 1rem; font-weight: 600; color: var(--text); margin: 1.5rem 0 .75rem; padding-bottom: .5rem; border-bottom: 1px solid var(--border); }}
    .modal-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 1000; align-items: center; justify-content: center; }}
    .modal-box {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 1.5rem; width: 90%; max-width: 420px; box-shadow: 0 8px 30px rgba(0,0,0,.2); }}
    .modal-box label {{ display: block; font-size: .8rem; font-weight: 500; color: var(--text-muted); margin-bottom: .25rem; margin-top: .75rem; }}
    .modal-box input {{ width: 100%; padding: .5rem .7rem; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--bg); color: var(--text); font-family: inherit; font-size: .9rem; box-sizing: border-box; }}
  ''')}
<body>
  <div class="app">
    {sidebar_html("admin", username=user.username, role=user.role)}
    <div class="main-content">
      {mobile_header_html("Admin")}
      <div class="main-topbar">
        <span class="main-topbar-title">User Administration</span>
        <button class="icon-btn" id="theme-toggle" onclick="toggleTheme()" title="Toggle theme"></button>
      </div>
      <div class="page-content">

        <!-- Create User -->
        <div class="admin-section-title">Create New User</div>
        <div class="change-card" style="max-width:480px;">
          <form id="create-user-form" style="display:flex;flex-direction:column;gap:.75rem;">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem;">
              <div class="form-group" style="margin:0;">
                <label for="new-username">Username</label>
                <input type="text" id="new-username" placeholder="jane.smith" required autocomplete="off">
              </div>
              <div class="form-group" style="margin:0;">
                <label for="new-password">Password</label>
                <input type="password" id="new-password" placeholder="Temporary password" required autocomplete="new-password">
              </div>
            </div>
            <div style="display:flex;align-items:flex-end;gap:.75rem;">
              <div class="form-group" style="margin:0;flex:1;">
                <label for="new-role">Role</label>
                <select id="new-role" style="width:100%;padding:.6rem .85rem;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-family:inherit;font-size:.9rem;">
                  {create_role_opts}
                </select>
              </div>
              <button type="submit" class="btn btn-primary" style="flex-shrink:0;">Create User</button>
            </div>
          </form>
        </div>

        <!-- Pending Registrations -->
        <div class="admin-section-title">Pending Registrations
          <span style="font-size:.75rem;font-weight:400;color:var(--text-muted);margin-left:.5rem;">{len(pending)} pending</span>
        </div>
        {pending_section}

        <!-- All Users -->
        <div class="admin-section-title">All Users
          <span style="font-size:.75rem;font-weight:400;color:var(--text-muted);margin-left:.5rem;">{len(users)} total</span>
        </div>
        <div style="overflow-x:auto;">
          <table class="admin-table">
            <thead><tr>
              <th>Username</th><th>Role</th><th>Display Name</th><th>Email</th><th>Created</th><th>Actions</th>
            </tr></thead>
            <tbody>{"".join(user_rows)}</tbody>
          </table>
        </div>

        <!-- Roles Management -->
        <div class="admin-section-title">Roles &amp; Permissions
          <span style="font-size:.75rem;font-weight:400;color:var(--text-muted);margin-left:.5rem;">{len(roles)} roles</span>
        </div>

        <div class="change-card" style="max-width:480px;margin-bottom:1rem;">
          <form id="add-role-form" style="display:flex;gap:.5rem;align-items:flex-end;flex-wrap:wrap;">
            <div style="flex:1;min-width:120px;">
              <label style="display:block;font-size:.78rem;font-weight:500;color:var(--text-muted);margin-bottom:.2rem;">New role name</label>
              <input type="text" id="new-role-name" placeholder="e.g. volunteer" required
                style="width:100%;padding:.4rem .6rem;font-size:.85rem;border:1px solid var(--border);
                border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-family:inherit;box-sizing:border-box;">
            </div>
            <div style="flex:2;min-width:160px;">
              <label style="display:block;font-size:.78rem;font-weight:500;color:var(--text-muted);margin-bottom:.2rem;">Description</label>
              <input type="text" id="new-role-desc" placeholder="Optional description"
                style="width:100%;padding:.4rem .6rem;font-size:.85rem;border:1px solid var(--border);
                border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-family:inherit;box-sizing:border-box;">
            </div>
            <button type="submit" class="btn btn-sm btn-primary">Add Role</button>
          </form>
        </div>

        {"".join(role_cards)}

      </div>
    </div>
  </div>

  <!-- Edit User Modal -->
  <div class="modal-overlay" id="edit-modal" onclick="if(event.target===this)closeEditModal()">
    <div class="modal-box">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.5rem;">
        <span id="edit-modal-title" style="font-weight:600;font-size:1rem;color:var(--text);">Edit User</span>
        <button onclick="closeEditModal()" style="background:none;border:none;font-size:1.2rem;cursor:pointer;color:var(--text-muted);padding:.2rem;">&times;</button>
      </div>
      <input type="hidden" id="edit-username">
      <label>Display Name</label>
      <input type="text" id="edit-display-name" placeholder="Full name">
      <label>Email</label>
      <input type="email" id="edit-email" placeholder="user@example.com">
      <label>Reset Password <span style="font-weight:400;color:var(--text-muted);">(leave blank to keep current)</span></label>
      <input type="password" id="edit-password" placeholder="New password" autocomplete="new-password">
      <div style="display:flex;justify-content:flex-end;gap:.5rem;margin-top:1rem;">
        <button class="btn btn-sm btn-ghost" onclick="closeEditModal()">Cancel</button>
        <button class="btn btn-sm btn-primary" onclick="saveUserInfo()">Save</button>
      </div>
    </div>
  </div>

  <script src="/static/js/sidebar.js"></script>
  <script src="/static/js/admin.js"></script>
</body>
</html>"""
    return HTMLResponse(html)


# ── Admin API endpoints ──────────────────────────────────────────────────────

@router.post("/api/admin/users")
async def admin_create_user(request: Request):
    user = get_full_user_from_cookie(request)
    if not user or not has_permission(user.role, "add_users"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    role = str(body.get("role", "staff"))
    if not username or not password:
        return JSONResponse({"detail": "Username and password required"}, status_code=400)
    try:
        create_user(username, password, role)
        log.info("Admin %s created user %s (role=%s)", user.username, username, role)
        return JSONResponse({"ok": True})
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.post("/api/admin/users/{username}/edit")
async def admin_edit_user(username: str, request: Request):
    user = get_full_user_from_cookie(request)
    if not user or not has_permission(user.role, "add_users"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    try:
        display_name = body.get("display_name")
        email = body.get("email")
        password = body.get("password")
        if display_name is not None or email is not None:
            update_user_info(username, display_name=display_name, email=email)
        if password:
            reset_user_password(username, password)
        log.info("Admin %s edited user %s", user.username, username)
        return JSONResponse({"ok": True})
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.post("/api/admin/users/{username}/role")
async def admin_set_role(username: str, request: Request):
    user = get_full_user_from_cookie(request)
    if not user or not has_permission(user.role, "add_users"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    new_role = str(body.get("role", ""))
    try:
        update_user_role(username, new_role)
        log.info("Admin %s changed %s role to %s", user.username, username, new_role)
        return JSONResponse({"ok": True})
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.post("/api/admin/users/{username}/suspend")
async def admin_suspend(username: str, request: Request):
    user = get_full_user_from_cookie(request)
    if not user or not has_permission(user.role, "add_users"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    suspend_user(username)
    log.info("Admin %s suspended %s", user.username, username)
    return JSONResponse({"ok": True})


@router.post("/api/admin/users/{username}/reactivate")
async def admin_reactivate(username: str, request: Request):
    user = get_full_user_from_cookie(request)
    if not user or not has_permission(user.role, "add_users"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    reactivate_user(username)
    log.info("Admin %s reactivated %s", user.username, username)
    return JSONResponse({"ok": True})


@router.post("/api/admin/registrations/{reg_id}/approve")
async def admin_approve_reg(reg_id: int, request: Request):
    user = get_full_user_from_cookie(request)
    if not user or not has_permission(user.role, "add_users"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    role = body.get("role") or None
    try:
        approve_registration(reg_id, approved_by=user.username, role=role)
        log.info("Admin %s approved registration %s", user.username, reg_id)
        return JSONResponse({"ok": True})
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.post("/api/admin/registrations/{reg_id}/reject")
async def admin_reject_reg(reg_id: int, request: Request):
    user = get_full_user_from_cookie(request)
    if not user or not has_permission(user.role, "add_users"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = str(body.get("reason", ""))
    try:
        reject_registration(reg_id, rejected_by=user.username, reason=reason)
        log.info("Admin %s rejected registration %s", user.username, reg_id)
        return JSONResponse({"ok": True})
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


# ── Roles API ────────────────────────────────────────────────────────────────

@router.post("/api/admin/roles")
async def admin_create_role(request: Request):
    user = get_full_user_from_cookie(request)
    if not user or not has_permission(user.role, "add_users"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    try:
        create_role(
            name=str(body.get("name", "")),
            permissions=body.get("permissions", []),
            description=str(body.get("description", "")),
        )
        log.info("Admin %s created role %s", user.username, body.get("name"))
        return JSONResponse({"ok": True})
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.post("/api/admin/roles/{role_name}")
async def admin_update_role(role_name: str, request: Request):
    user = get_full_user_from_cookie(request)
    if not user or not has_permission(user.role, "add_users"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    try:
        update_role(
            name=role_name,
            permissions=body.get("permissions", []),
            description=body.get("description"),
        )
        log.info("Admin %s updated role %s", user.username, role_name)
        return JSONResponse({"ok": True})
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.delete("/api/admin/roles/{role_name}")
async def admin_delete_role(role_name: str, request: Request):
    user = get_full_user_from_cookie(request)
    if not user or not has_permission(user.role, "add_users"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    try:
        delete_role(role_name)
        log.info("Admin %s deleted role %s", user.username, role_name)
        return JSONResponse({"ok": True})
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
