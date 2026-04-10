"""
Web dashboard page and stats API — token usage analytics with Chart.js.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.auth.auth import has_permission
from app.config import settings
from app.interfaces.web_auth import get_full_user_from_cookie
from app.interfaces.web_templates import mobile_header_html, page_head, sidebar_html

log = logging.getLogger(__name__)

router = APIRouter()


# ── Dashboard stats API ──────────────────────────────────────────────────────

@router.get("/api/admin/dashboard/stats")
async def api_dashboard_stats(request: Request, period: str = "24h"):
    """Return aggregated token usage stats for the dashboard."""
    user = get_full_user_from_cookie(request)
    if not user or not has_permission(user.role, "add_users"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)

    import sqlite3 as _sqlite3

    period_map = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "90d": timedelta(days=90),
        "all": timedelta(days=3650),
    }
    delta = period_map.get(period, timedelta(hours=24))
    cutoff = (datetime.utcnow() - delta).isoformat()

    db_path = settings.audit_log_path
    conn = _sqlite3.connect(db_path)
    conn.row_factory = _sqlite3.Row

    # Determine bucket size based on period
    if period in ("24h",):
        bucket_fmt = "%Y-%m-%dT%H"
        bucket_label = "hour"
    else:
        bucket_fmt = "%Y-%m-%d"
        bucket_label = "day"

    # Query audit_log table
    timeline_rows = conn.execute("""
        SELECT
            strftime(?, ts) AS bucket,
            COUNT(*) AS call_count,
            SUM(COALESCE(json_extract(detail, '$.prompt_tokens'), 0)) AS prompt_tokens,
            SUM(COALESCE(json_extract(detail, '$.completion_tokens'), 0)) AS completion_tokens,
            AVG(COALESCE(json_extract(detail, '$.latency_ms'), 0)) AS avg_latency_ms
        FROM audit_log
        WHERE actor = 'llm_client' AND ts >= ?
        GROUP BY bucket
        ORDER BY bucket
    """, (bucket_fmt, cutoff)).fetchall()

    # Also query llm_calls table
    llm_timeline = conn.execute("""
        SELECT
            strftime(?, ts) AS bucket,
            COUNT(*) AS call_count,
            SUM(prompt_tokens) AS prompt_tokens,
            SUM(completion_tokens) AS completion_tokens,
            AVG(latency_ms) AS avg_latency_ms
        FROM llm_calls
        WHERE ts >= ?
        GROUP BY bucket
        ORDER BY bucket
    """, (bucket_fmt, cutoff)).fetchall()

    # Merge both sources
    merged_timeline: dict[str, dict] = {}
    for row in timeline_rows:
        b = row["bucket"]
        merged_timeline[b] = {
            "bucket": b,
            "call_count": row["call_count"] or 0,
            "prompt_tokens": row["prompt_tokens"] or 0,
            "completion_tokens": row["completion_tokens"] or 0,
            "avg_latency_ms": round(row["avg_latency_ms"] or 0),
        }
    for row in llm_timeline:
        b = row["bucket"]
        if b in merged_timeline:
            merged_timeline[b]["call_count"] += row["call_count"] or 0
            merged_timeline[b]["prompt_tokens"] += row["prompt_tokens"] or 0
            merged_timeline[b]["completion_tokens"] += row["completion_tokens"] or 0
        else:
            merged_timeline[b] = {
                "bucket": b,
                "call_count": row["call_count"] or 0,
                "prompt_tokens": row["prompt_tokens"] or 0,
                "completion_tokens": row["completion_tokens"] or 0,
                "avg_latency_ms": round(row["avg_latency_ms"] or 0),
            }
    timeline = [merged_timeline[k] for k in sorted(merged_timeline)]

    # ── Per-user stats ────────────────────────────────────────────────
    user_stats_audit = conn.execute("""
        SELECT
            COALESCE(json_extract(detail, '$.user_id'), actor) AS uid,
            COUNT(*) AS call_count,
            SUM(COALESCE(json_extract(detail, '$.prompt_tokens'), 0)) AS prompt_tokens,
            SUM(COALESCE(json_extract(detail, '$.completion_tokens'), 0)) AS completion_tokens,
            AVG(COALESCE(json_extract(detail, '$.latency_ms'), 0)) AS avg_latency_ms
        FROM audit_log
        WHERE actor = 'llm_client' AND ts >= ?
        GROUP BY uid
        ORDER BY (COALESCE(json_extract(detail, '$.prompt_tokens'), 0) + COALESCE(json_extract(detail, '$.completion_tokens'), 0)) DESC
    """, (cutoff,)).fetchall()

    user_stats_llm = conn.execute("""
        SELECT
            user_id AS uid,
            COUNT(*) AS call_count,
            SUM(prompt_tokens) AS prompt_tokens,
            SUM(completion_tokens) AS completion_tokens,
            AVG(latency_ms) AS avg_latency_ms
        FROM llm_calls
        WHERE ts >= ? AND user_id != ''
        GROUP BY user_id
        ORDER BY (prompt_tokens + completion_tokens) DESC
    """, (cutoff,)).fetchall()

    # Merge user stats
    user_map: dict[str, dict] = {}
    for row in list(user_stats_audit) + list(user_stats_llm):
        uid = row["uid"] or "system"
        display = uid.replace("web:", "") if uid.startswith("web:") else uid
        if display in user_map:
            user_map[display]["call_count"] += row["call_count"] or 0
            user_map[display]["prompt_tokens"] += row["prompt_tokens"] or 0
            user_map[display]["completion_tokens"] += row["completion_tokens"] or 0
        else:
            user_map[display] = {
                "user": display,
                "call_count": row["call_count"] or 0,
                "prompt_tokens": row["prompt_tokens"] or 0,
                "completion_tokens": row["completion_tokens"] or 0,
                "avg_latency_ms": round(row["avg_latency_ms"] or 0),
            }
    users_by_tokens = sorted(user_map.values(), key=lambda u: u["prompt_tokens"] + u["completion_tokens"], reverse=True)

    # ── Totals ────────────────────────────────────────────────────────
    total_prompt = sum(u["prompt_tokens"] for u in users_by_tokens)
    total_completion = sum(u["completion_tokens"] for u in users_by_tokens)
    total_calls = sum(u["call_count"] for u in users_by_tokens)

    # ── Skill breakdown ───────────────────────────────────────────────
    skill_rows = conn.execute("""
        SELECT
            COALESCE(json_extract(detail, '$.skill_used'), 'unknown') AS skill,
            COUNT(*) AS call_count,
            SUM(COALESCE(json_extract(detail, '$.prompt_tokens'), 0)) AS prompt_tokens,
            SUM(COALESCE(json_extract(detail, '$.completion_tokens'), 0)) AS completion_tokens
        FROM audit_log
        WHERE actor = 'llm_client' AND ts >= ?
        GROUP BY skill
        ORDER BY call_count DESC
    """, (cutoff,)).fetchall()
    skills = [{"skill": r["skill"] or "general", "call_count": r["call_count"],
               "prompt_tokens": r["prompt_tokens"] or 0, "completion_tokens": r["completion_tokens"] or 0}
              for r in skill_rows]

    # ── Auth events ───────────────────────────────────────────────────
    auth_rows = conn.execute("""
        SELECT event_type, COUNT(*) AS cnt
        FROM auth_events
        WHERE ts >= ?
        GROUP BY event_type
    """, (cutoff,)).fetchall()
    auth_summary = {r["event_type"]: r["cnt"] for r in auth_rows}

    # ── Document ops ──────────────────────────────────────────────────
    doc_rows = conn.execute("""
        SELECT operation, COUNT(*) AS cnt
        FROM document_ops
        WHERE ts >= ?
        GROUP BY operation
    """, (cutoff,)).fetchall()
    doc_summary = {r["operation"]: r["cnt"] for r in doc_rows}

    # ── Dream cycles ──────────────────────────────────────────────────
    dream_rows = conn.execute("""
        SELECT COUNT(*) AS cycles,
               SUM(tasks_completed) AS completed,
               SUM(tasks_failed) AS failed
        FROM dream_cycles
        WHERE ts >= ?
    """, (cutoff,)).fetchall()
    dream_summary = dict(dream_rows[0]) if dream_rows else {}

    conn.close()

    # ── Cost calculation (GLM-5V-Turbo via SiliconFlow) ───────────────
    INPUT_COST_PER_M = 1.20
    OUTPUT_COST_PER_M = 4.00
    input_cost = (total_prompt / 1_000_000) * INPUT_COST_PER_M
    output_cost = (total_completion / 1_000_000) * OUTPUT_COST_PER_M
    total_cost = input_cost + output_cost

    period_days = {"24h": 1, "7d": 7, "30d": 30, "90d": 90, "all": None}
    days = period_days.get(period)
    if days and days > 0:
        daily_cost = total_cost / days
        projected_monthly = daily_cost * 30
    else:
        projected_monthly = None

    return JSONResponse({
        "period": period,
        "bucket_label": bucket_label,
        "timeline": timeline,
        "users": users_by_tokens,
        "top_users": users_by_tokens[:10],
        "skills": skills,
        "totals": {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "call_count": total_calls,
        },
        "cost": {
            "input_cost": round(input_cost, 4),
            "output_cost": round(output_cost, 4),
            "total_cost": round(total_cost, 4),
            "projected_monthly": round(projected_monthly, 2) if projected_monthly is not None else None,
            "currency": "USD",
            "pricing": {
                "model": "GLM-5V-Turbo",
                "input_per_m": INPUT_COST_PER_M,
                "output_per_m": OUTPUT_COST_PER_M,
            },
        },
        "auth_events": auth_summary,
        "document_ops": doc_summary,
        "dream_cycles": dream_summary,
    })


# ── Dashboard page ───────────────────────────────────────────────────────────

@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    user = get_full_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not has_permission(user.role, "add_users"):
        return HTMLResponse("<p>Access denied.</p>", status_code=403)

    return f"""<!DOCTYPE html>
<html lang="en">
{page_head("Dashboard — CDCN Agent")}
<body>
  <div class="app">
    {sidebar_html("dashboard", username=user.username, role=user.role)}
    <div class="main-content">
      {mobile_header_html("Dashboard")}
      <div class="main-topbar">
        <h1 style="font-size:1.1rem;font-weight:600;margin:0;">Dashboard</h1>
        <div style="display:flex;gap:.5rem;align-items:center;">
          <select id="period-select" style="width:auto;font-size:.82rem;padding:.35rem .6rem;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);color:var(--text);font-family:inherit;">
            <option value="24h">Last 24 Hours</option>
            <option value="7d">Last 7 Days</option>
            <option value="30d" selected>Last 30 Days</option>
            <option value="90d">Last 90 Days</option>
            <option value="all">All Time</option>
          </select>
          <button class="btn btn-secondary" style="font-size:.82rem;padding:.35rem .75rem;" onclick="loadDashboard()">Refresh</button>
        </div>
      </div>
      <div class="page-content" id="dashboard-content" style="overflow-y:auto;padding:1.25rem;">

        <!-- Summary cards -->
        <div id="summary-cards" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.75rem;margin-bottom:1.25rem;"></div>

        <!-- Token usage over time chart -->
        <div class="card" style="margin-bottom:1.25rem;">
          <h3 style="font-size:.92rem;font-weight:600;margin:0 0 .75rem;">Token Usage Over Time</h3>
          <div style="position:relative;height:250px;"><canvas id="token-timeline-chart"></canvas></div>
        </div>

        <!-- Top users chart -->
        <div class="card" style="margin-bottom:1.25rem;">
          <h3 style="font-size:.92rem;font-weight:600;margin:0 0 .75rem;">Top 10 Users by Token Usage</h3>
          <div style="position:relative;height:220px;"><canvas id="top-users-chart"></canvas></div>
        </div>

        <!-- Skill breakdown -->
        <div class="card" style="margin-bottom:1.25rem;">
          <h3 style="font-size:.92rem;font-weight:600;margin:0 0 .75rem;">Usage by Skill</h3>
          <div style="position:relative;height:200px;"><canvas id="skills-chart"></canvas></div>
        </div>

        <!-- Activity summary -->
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:.75rem;margin-bottom:1.25rem;">
          <div class="card">
            <h3 style="font-size:.92rem;font-weight:600;margin:0 0 .5rem;">Auth Events</h3>
            <div id="auth-events-list" style="font-size:.84rem;"></div>
          </div>
          <div class="card">
            <h3 style="font-size:.92rem;font-weight:600;margin:0 0 .5rem;">Document Operations</h3>
            <div id="doc-ops-list" style="font-size:.84rem;"></div>
          </div>
          <div class="card">
            <h3 style="font-size:.92rem;font-weight:600;margin:0 0 .5rem;">Dream Cycles</h3>
            <div id="dream-cycles-info" style="font-size:.84rem;"></div>
          </div>
        </div>

        <!-- Full user table -->
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.5rem;">
            <h3 style="font-size:.92rem;font-weight:600;margin:0;">All User Usage</h3>
            <button class="btn btn-secondary" style="font-size:.78rem;padding:.25rem .6rem;" id="toggle-table-btn" onclick="toggleUserTable()">Show Table</button>
          </div>
          <div id="user-table-wrap" style="display:none;overflow-x:auto;">
            <table id="user-table" style="width:100%;border-collapse:collapse;font-size:.82rem;">
              <thead>
                <tr style="border-bottom:2px solid var(--border);text-align:left;">
                  <th style="padding:.4rem .5rem;cursor:pointer;" onclick="sortTable(0)">User</th>
                  <th style="padding:.4rem .5rem;cursor:pointer;text-align:right;" onclick="sortTable(1)">Calls</th>
                  <th style="padding:.4rem .5rem;cursor:pointer;text-align:right;" onclick="sortTable(2)">Input Tokens</th>
                  <th style="padding:.4rem .5rem;cursor:pointer;text-align:right;" onclick="sortTable(3)">Output Tokens</th>
                  <th style="padding:.4rem .5rem;cursor:pointer;text-align:right;" onclick="sortTable(4)">Total Tokens</th>
                  <th style="padding:.4rem .5rem;cursor:pointer;text-align:right;" onclick="sortTable(5)">Avg Latency</th>
                </tr>
              </thead>
              <tbody id="user-table-body"></tbody>
            </table>
          </div>
        </div>

      </div>
    </div>
  </div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script src="/static/js/sidebar.js"></script>
<script src="/static/js/dashboard.js"></script>
</body>
</html>"""
