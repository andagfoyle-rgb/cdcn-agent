"""
Token usage tracking and cost estimation.

Logs per-call token usage to a dedicated SQLite table and provides
cost aggregation for the admin dashboard and daily alerts.

SiliconFlow pricing (per 1K tokens):
  - Fresh input:  $0.0012/K
  - Cached input:  $0.00003/K  (40x cheaper)
  - Output:        $0.004/K
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.config import settings

log = logging.getLogger(__name__)

# ── Cost rates (per token, not per 1K) ────────────────────────────────────────

RATE_INPUT = 0.0012 / 1000       # $0.0012 per 1K input tokens
RATE_CACHED = 0.00003 / 1000     # $0.00003 per 1K cached tokens
RATE_OUTPUT = 0.004 / 1000       # $0.004 per 1K output tokens

DAILY_COST_WARNING = 2.00        # dollars — post alert if exceeded

# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS token_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    user_id         TEXT,
    thread_id       TEXT,
    query_class     TEXT,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cached_tokens   INTEGER NOT NULL DEFAULT 0,
    estimated_cost  REAL    NOT NULL DEFAULT 0.0,
    model           TEXT,
    skill_used      TEXT
);
CREATE INDEX IF NOT EXISTS idx_token_usage_ts ON token_usage(ts);
CREATE INDEX IF NOT EXISTS idx_token_usage_date ON token_usage(substr(ts, 1, 10));
"""


async def _db():
    """Open the audit DB and ensure token_usage table exists."""
    db_path = settings.audit_log_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    await conn.executescript(_DDL)
    return conn


def estimate_cost(input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    """Calculate estimated cost in dollars."""
    fresh_input = max(0, input_tokens - cached_tokens)
    return (
        fresh_input * RATE_INPUT
        + cached_tokens * RATE_CACHED
        + output_tokens * RATE_OUTPUT
    )


async def log_token_usage(
    *,
    user_id: str = "",
    thread_id: str = "",
    query_class: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    model: str = "",
    skill_used: str = "",
) -> float:
    """
    Log a single LLM call's token usage and return estimated cost.
    """
    cost = estimate_cost(input_tokens, output_tokens, cached_tokens)
    ts = datetime.now(timezone.utc).isoformat()

    try:
        conn = await _db()
        try:
            await conn.execute(
                "INSERT INTO token_usage "
                "(ts, user_id, thread_id, query_class, input_tokens, output_tokens, "
                "cached_tokens, estimated_cost, model, skill_used) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, user_id, thread_id, query_class, input_tokens, output_tokens,
                 cached_tokens, cost, model, skill_used),
            )
            await conn.commit()
        finally:
            await conn.close()
    except Exception as exc:
        log.warning("Failed to log token usage: %s", exc)

    return cost


async def get_today_usage() -> dict:
    """
    Return today's aggregated token usage and cost breakdown.

    Returns:
        {
            "date": "2026-04-10",
            "total_calls": int,
            "total_input_tokens": int,
            "total_output_tokens": int,
            "total_cached_tokens": int,
            "total_cost": float,
            "by_query_class": { "simple_chat": {...}, "knowledge": {...}, ... },
            "by_user": { "alice": {...}, ... },
        }
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        conn = await _db()
        try:
            conn.row_factory = aiosqlite.Row

            # Overall totals
            cursor = await conn.execute(
                "SELECT "
                "  COUNT(*) as total_calls, "
                "  COALESCE(SUM(input_tokens), 0) as total_input, "
                "  COALESCE(SUM(output_tokens), 0) as total_output, "
                "  COALESCE(SUM(cached_tokens), 0) as total_cached, "
                "  COALESCE(SUM(estimated_cost), 0.0) as total_cost "
                "FROM token_usage WHERE ts LIKE ?",
                (f"{today}%",),
            )
            row = await cursor.fetchone()

            result = {
                "date": today,
                "total_calls": row["total_calls"],
                "total_input_tokens": row["total_input"],
                "total_output_tokens": row["total_output"],
                "total_cached_tokens": row["total_cached"],
                "total_cost": round(row["total_cost"], 4),
            }

            # By query class
            cursor = await conn.execute(
                "SELECT query_class, "
                "  COUNT(*) as calls, "
                "  COALESCE(SUM(input_tokens), 0) as input_tok, "
                "  COALESCE(SUM(output_tokens), 0) as output_tok, "
                "  COALESCE(SUM(cached_tokens), 0) as cached_tok, "
                "  COALESCE(SUM(estimated_cost), 0.0) as cost "
                "FROM token_usage WHERE ts LIKE ? GROUP BY query_class",
                (f"{today}%",),
            )
            by_class = {}
            async for r in cursor:
                by_class[r["query_class"] or "unknown"] = {
                    "calls": r["calls"],
                    "input_tokens": r["input_tok"],
                    "output_tokens": r["output_tok"],
                    "cached_tokens": r["cached_tok"],
                    "cost": round(r["cost"], 4),
                }
            result["by_query_class"] = by_class

            # By user
            cursor = await conn.execute(
                "SELECT user_id, "
                "  COUNT(*) as calls, "
                "  COALESCE(SUM(estimated_cost), 0.0) as cost "
                "FROM token_usage WHERE ts LIKE ? GROUP BY user_id",
                (f"{today}%",),
            )
            by_user = {}
            async for r in cursor:
                by_user[r["user_id"] or "system"] = {
                    "calls": r["calls"],
                    "cost": round(r["cost"], 4),
                }
            result["by_user"] = by_user

            return result
        finally:
            await conn.close()
    except Exception as exc:
        log.warning("Failed to get today's usage: %s", exc)
        return {
            "date": today,
            "total_calls": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cached_tokens": 0,
            "total_cost": 0.0,
            "by_query_class": {},
            "by_user": {},
        }


async def check_daily_cost_alert() -> str | None:
    """
    Check if today's cost exceeds the warning threshold.
    Returns a warning message string, or None if under budget.
    """
    usage = await get_today_usage()
    total = usage["total_cost"]
    if total >= DAILY_COST_WARNING:
        return (
            f"Daily API cost alert: today's spend is ${total:.2f}, "
            f"which exceeds the ${DAILY_COST_WARNING:.2f} warning threshold. "
            f"Total calls: {usage['total_calls']}, "
            f"input tokens: {usage['total_input_tokens']:,}, "
            f"output tokens: {usage['total_output_tokens']:,}."
        )
    return None
