"""
Web HTML template generation — sidebar, page head, login/register forms, page shells.

All functions return HTML strings. No route handlers or business logic here.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.auth.auth import has_permission


# ── Mini calendar ────────────────────────────────────────────────────────────

def mini_calendar_html() -> str:
    """Render a small calendar for the current month."""
    import calendar as _cal
    today = datetime.now()
    year, month = today.year, today.month
    month_name = today.strftime("%B %Y")
    day_names = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    header_cells = "".join(f'<div class="mini-cal-day-name">{d}</div>' for d in day_names)
    weeks = _cal.monthcalendar(year, month)
    day_cells = ""
    for week in weeks:
        for day in week:
            if day == 0:
                day_cells += '<div class="mini-cal-day other-month"></div>'
            elif day == today.day:
                day_cells += f'<div class="mini-cal-day today">{day}</div>'
            else:
                day_cells += f'<div class="mini-cal-day">{day}</div>'
    return f"""<div class="mini-cal">
      <div class="mini-cal-header">{month_name}</div>
      <div class="mini-cal-grid">{header_cells}{day_cells}</div>
    </div>"""


# ── Sidebar ──────────────────────────────────────────────────────────────────

def sidebar_html(active: str = "chat", username: str = "", role: str = "") -> str:
    """Build the main sidebar navigation HTML."""
    icon = lambda paths: f'<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24">{paths}</svg>'
    icons = {
        "chat":     icon('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
        "archive":  icon('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'),
        "calendar": icon('<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>'),
        "actions":  icon('<polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'),
        "funding":  icon('<path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>'),
        "memory":   icon('<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>'),
        "pending":  icon('<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>'),
        "admin":    icon('<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>'),
        "dashboard": icon('<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>'),
        "logout":   icon('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>'),
    }
    nav_items = [
        ("chat",     "/",                 "chat",     "Chat"),
        ("archive",  "/archive",          "archive",  "Archive"),
        ("calendar", "/calendar",         "calendar", "Calendar"),
        ("actions",  "/action-points",    "actions",  "Action Points"),
        ("funding",  "/funding",          "funding",  "Funding"),
        ("memory",   "/memory",           "memory",   "Memory"),
        ("pending",  "/pending-changes",  "pending",  "Changes"),
    ]
    links = []
    for key, href, icon_key, label in nav_items:
        cls = "nav-link active" if key == active else "nav-link"
        links.append(f'<a href="{href}" class="{cls}">{icons[icon_key]}{label}</a>')
    if has_permission(role, "add_users"):
        cls = "nav-link active" if active == "dashboard" else "nav-link"
        links.append(f'<a href="/admin/dashboard" class="{cls}">{icons["dashboard"]}Dashboard</a>')
        cls = "nav-link active" if active == "admin" else "nav-link"
        links.append(f'<a href="/admin" class="{cls}">{icons["admin"]}Admin</a>')

    initials = (username[0].upper() if username else "U")
    cal_html = mini_calendar_html()

    return f"""
    <aside class="sidebar" id="sidebar" role="complementary" aria-label="Sidebar navigation">
      <div class="sidebar-header">
        <div class="sidebar-logo-mark" aria-hidden="true">
          <svg width="14" height="14" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>
        </div>
        <h2>CDCN Agent</h2>
      </div>
      <nav class="sidebar-body" aria-label="Main navigation">
        <div class="sidebar-section" id="status-heading">Status</div>
        <div style="padding:0 .1rem .25rem;">
          <div class="status-container" id="status-container">
            <div class="status-dot" id="status-dot"></div>
            <span class="status-label" id="status-label">Loading&hellip;</span>
          </div>
        </div>
        <div class="sidebar-section">Navigation</div>
        {"".join(links)}
        <div class="sidebar-section" style="margin-top:.5rem;">Online Now</div>
        <div id="online-users-list" style="padding:0 .1rem .1rem; display:flex; flex-direction:column; gap:.2rem;">
          <span style="font-size:.72rem;color:var(--text-subtle);padding:.1rem .5rem;">Loading&hellip;</span>
        </div>
        <div class="sidebar-section" style="margin-top:.25rem;">Calendar</div>
        {cal_html}
      </nav>
      <div class="sidebar-footer">
        <a href="/logout" class="user-profile" aria-label="Sign out as {username or 'User'}">
          <div class="user-avatar" aria-hidden="true">{initials}</div>
          <div class="user-info">
            <div class="user-name">{username or "User"}</div>
            <div class="user-action">Sign out</div>
          </div>
          {icons['logout']}
        </a>
      </div>
    </aside>
    <div class="overlay" id="overlay" onclick="closeSidebar()" role="presentation"></div>
"""


# ── Page head ────────────────────────────────────────────────────────────────

def page_head(title: str, extra_style: str = "") -> str:
    """Shared <head> block for every page — includes Tailwind CDN, stylesheet, and theme init."""
    extra = f"\n  <style>{extra_style}</style>" if extra_style else ""
    return f"""<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {{
      darkMode: ['selector', '[data-theme="dark"]'],
      theme: {{
        extend: {{
          colors: {{
            surface: 'var(--surface)',
            'surface-2': 'var(--surface-2)',
            border: 'var(--border)',
            primary: 'var(--primary)',
            'primary-hover': 'var(--primary-hover)',
          }},
          fontFamily: {{
            sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
          }},
        }},
      }},
    }}
  </script>
  <link rel="stylesheet" href="/static/css/main.css">
  <script>(function(){{var t=localStorage.getItem('theme')||'light';document.documentElement.setAttribute('data-theme',t);}})();</script>{extra}
</head>"""


# ── Mobile header ────────────────────────────────────────────────────────────

def mobile_header_html(title: str = "CDCN Agent") -> str:
    """Mobile hamburger menu header."""
    return f"""
    <div class="mobile-header">
      <button class="hamburger" onclick="toggleSidebar()" aria-label="Menu">
        <span></span><span></span><span></span>
      </button>
      <h2>{title}</h2>
    </div>
"""


# ── Login / Register / Forgot password HTML ──────────────────────────────────

def login_html() -> str:
    """Return the login page HTML."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
{page_head("CDCN Agent — Sign In")}
<body class="login-page">
  <div class="login-box">
    <div class="login-logo">
      <div class="login-logo-mark">
        <svg width="18" height="18" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>
      </div>
      <div>
        <h1>CDCN Agent</h1>
        <p>Community Development Company Nesting</p>
      </div>
    </div>
    <form method="post" action="/login">
      <div class="form-group">
        <label for="username">Username</label>
        <input type="text" id="username" name="username" required autofocus autocomplete="username">
      </div>
      <div class="form-group">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" required autocomplete="current-password">
      </div>
      <button type="submit" class="btn btn-primary btn-full" style="margin-top:.25rem;">Sign in</button>
    </form>
    <!-- ERROR_PLACEHOLDER -->
    <div class="login-links">
      <a href="/forgot-password">Forgot password?</a>
      <a href="/register">Create account</a>
    </div>
  </div>
</body>
</html>"""


def register_html() -> str:
    """Return the registration page HTML."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
{page_head("CDCN Agent — Create Account")}
<body class="login-page">
  <div class="login-box">
    <a href="/login" class="back-link">
      <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg>
      Back to sign in
    </a>
    <div class="login-logo">
      <div class="login-logo-mark">
        <svg width="18" height="18" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>
      </div>
      <div>
        <h1>Create Account</h1>
        <p>Register for CDCN Agent access</p>
      </div>
    </div>
    <form method="post" action="/register">
      <div class="form-group">
        <label for="display_name">Full name</label>
        <input type="text" id="display_name" name="display_name" required autofocus autocomplete="name">
      </div>
      <div class="form-group">
        <label for="email">Email</label>
        <input type="email" id="email" name="email" required autocomplete="email">
      </div>
      <div class="form-group">
        <label for="username">Username</label>
        <input type="text" id="username" name="username" required autocomplete="username">
      </div>
      <div class="form-group">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" required autocomplete="new-password" minlength="8">
      </div>
      <div class="form-group">
        <label for="confirm_password">Confirm password</label>
        <input type="password" id="confirm_password" name="confirm_password" required autocomplete="new-password" minlength="8">
      </div>
      <button type="submit" class="btn btn-primary btn-full" style="margin-top:.25rem;">Request account</button>
    </form>
    <!-- REGISTER_PLACEHOLDER -->
  </div>
</body>
</html>"""


def forgot_html() -> str:
    """Return the forgot password page HTML."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
{page_head("CDCN Agent — Forgot Password")}
<body class="login-page">
  <div class="login-box">
    <a href="/login" class="back-link">
      <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg>
      Back to sign in
    </a>
    <div class="login-logo">
      <div class="login-logo-mark">
        <svg width="18" height="18" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>
      </div>
      <div>
        <h1>Forgot Password</h1>
        <p>Reset your account password</p>
      </div>
    </div>
    <div class="info-box">
      To reset your password, please contact a CDCN Agent administrator.
      They can reset your password from the admin panel.
    </div>
    <a href="/login" class="btn btn-ghost btn-full" style="margin-top:1.25rem;">Return to sign in</a>
  </div>
</body>
</html>"""


# ── Utility helpers ──────────────────────────────────────────────────────────

def esc(s: str) -> str:
    """Escape for safe HTML attribute embedding."""
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_size(n: int) -> str:
    """Human-readable file size."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n/1024:.1f} KB"
    return f"{n/1024**2:.1f} MB"


def file_icon(name: str) -> str:
    """Return an SVG icon appropriate for the file extension."""
    ext = Path(name).suffix.lower()
    icons = {
        ".pdf":  """<svg class="file-icon" width="18" height="18" fill="none" stroke="#ef4444" stroke-width="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>""",
        ".docx": """<svg class="file-icon" width="18" height="18" fill="none" stroke="#2563eb" stroke-width="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>""",
        ".xlsx": """<svg class="file-icon" width="18" height="18" fill="none" stroke="#16a34a" stroke-width="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>""",
        ".jpg":  """<svg class="file-icon" width="18" height="18" fill="none" stroke="#d97706" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>""",
        ".jpeg": """<svg class="file-icon" width="18" height="18" fill="none" stroke="#d97706" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>""",
        ".png":  """<svg class="file-icon" width="18" height="18" fill="none" stroke="#d97706" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>""",
    }
    default = """<svg class="file-icon" width="18" height="18" fill="none" stroke="#64748b" stroke-width="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>"""
    return icons.get(ext, default)


FOLDER_ICON = """<svg class="file-icon" width="18" height="18" fill="none" stroke="#f59e0b" stroke-width="2" viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>"""

ROLE_COLOURS = {"admin": "#7c3aed", "staff": "#2563eb", "trustee": "#0891b2"}


def role_badge(role: str) -> str:
    """Render a coloured role badge."""
    colour = ROLE_COLOURS.get(role, "#71717a")
    return (f'<span style="font-size:.68rem;font-weight:600;padding:.15rem .45rem;'
            f'border-radius:20px;background:{colour}18;color:{colour};">{role}</span>')


def funding_badge(relevance: str) -> str:
    """Render a funding relevance badge."""
    return f'<span class="funding-badge {relevance}">{relevance}</span>'
