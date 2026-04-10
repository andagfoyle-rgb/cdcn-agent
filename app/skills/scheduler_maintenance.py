"""
scheduler_maintenance — Maintenance and infrastructure scheduled jobs.

Contains nightly backup, database maintenance, disk monitoring,
and backup verification jobs for the CDCN Agent scheduler.
"""
from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.skills.scheduler_helpers import (
    _with_retry,
    _notify,
    _audit_start,
    _audit_end,
)

log = logging.getLogger(__name__)


# ── Job: nightly backup ──────────────────────────────────────────────────────


@_with_retry(max_retries=1, backoff_base=5.0)
async def _nightly_backup_job() -> None:
    """
    Daily at 04:00 (dream mode) — back up databases, ChromaDB, and config.

    NOT wake-gated: runs during dream mode to avoid I/O contention.
    Retains last 7 backups; older ones are pruned automatically.
    """
    t0 = time.monotonic()
    await _audit_start("nightly_backup")
    log.info("nightly_backup_job: starting")

    try:
        from app.utils.backup import run_backup
        summary = run_backup()
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "nightly_backup_job: complete in %dms — %d items, %d errors, %d pruned",
            duration_ms, len(summary["copied"]), len(summary["errors"]), summary["pruned"],
        )
        await _audit_end(
            "nightly_backup", duration_ms,
            f"copied={len(summary['copied'])} errors={len(summary['errors'])} "
            f"pruned={summary['pruned']}",
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.exception("nightly_backup_job: error after %dms: %s", duration_ms, exc)
        await _audit_end("nightly_backup", duration_ms, f"error={exc!r}")


# ── Job: weekly database maintenance ─────────────────────────────────────────


@_with_retry()
async def _db_maintenance_job() -> None:
    """
    Weekly (Sunday 02:00) — VACUUM, ANALYZE, integrity check on all SQLite databases.
    Logs sizes and alerts if any exceed 500 MB.
    """
    t0 = time.monotonic()
    await _audit_start("db_maintenance")
    log.info("db_maintenance_job: starting")

    db_dir = Path(settings.audit_log_path).parent
    db_files = sorted(db_dir.glob("*.db"))
    results = []

    for db_file in db_files:
        name = db_file.name
        size_mb = db_file.stat().st_size / (1024 * 1024)
        try:
            conn = sqlite3.connect(str(db_file))
            # Integrity check
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            # VACUUM and ANALYZE
            conn.execute("VACUUM")
            conn.execute("ANALYZE")
            conn.close()

            new_size_mb = db_file.stat().st_size / (1024 * 1024)
            status = "ok" if integrity == "ok" else f"INTEGRITY_FAIL: {integrity}"
            results.append(f"{name}: {size_mb:.1f}MB->{new_size_mb:.1f}MB ({status})")

            if new_size_mb > 500:
                log.warning("db_maintenance: %s exceeds 500MB (%.1fMB)", name, new_size_mb)
        except Exception as exc:
            results.append(f"{name}: error — {exc}")
            log.error("db_maintenance: %s failed: %s", name, exc)

    duration_ms = int((time.monotonic() - t0) * 1000)
    summary = "; ".join(results)
    log.info("db_maintenance_job: done in %dms — %s", duration_ms, summary)
    await _audit_end("db_maintenance", duration_ms, summary)


# ── Job: disk space monitoring ───────────────────────────────────────────────


@_with_retry()
async def _disk_monitor_job() -> None:
    """
    Daily at 08:35 — check available disk space on the Pi.
    Posts warnings to the noticeboard if space is low.
    """
    t0 = time.monotonic()
    await _audit_start("disk_monitor")

    total, used, free = shutil.disk_usage("/")
    free_gb = free / (1024 ** 3)
    total_gb = total / (1024 ** 3)
    pct_used = (used / total) * 100

    duration_ms = int((time.monotonic() - t0) * 1000)

    if free_gb < 0.5:
        msg = (
            f"\U0001f6a8 **CRITICAL: Disk space critically low** — "
            f"{free_gb:.1f} GB free of {total_gb:.1f} GB ({pct_used:.0f}% used). "
            f"Immediate action required to prevent data loss."
        )
        log.critical("disk_monitor: CRITICAL — %.1f GB free", free_gb)
        await _notify(msg)
    elif free_gb < 1.0:
        msg = (
            f"\u26a0\ufe0f **Warning: Disk space running low** — "
            f"{free_gb:.1f} GB free of {total_gb:.1f} GB ({pct_used:.0f}% used). "
            f"Consider cleaning up old backups or logs."
        )
        log.warning("disk_monitor: LOW — %.1f GB free", free_gb)
        await _notify(msg)
    else:
        log.info("disk_monitor: %.1f GB free (%.0f%% used)", free_gb, pct_used)

    await _audit_end("disk_monitor", duration_ms, f"free={free_gb:.1f}GB used={pct_used:.0f}%")


# ── Job: backup verification ─────────────────────────────────────────────────


@_with_retry()
async def _backup_verify_job() -> None:
    """
    Daily at 05:00 — verify nightly backup was created and is valid.
    Tests restoring to temp location and runs integrity check.
    Manages retention: 7 daily, 4 weekly (Sun), 3 monthly (1st).
    """
    t0 = time.monotonic()
    await _audit_start("backup_verify")
    log.info("backup_verify_job: starting")

    backup_dir = Path(settings.backup_path)
    if not backup_dir.exists():
        await _audit_end("backup_verify", 0, "error=backup_dir_missing")
        return

    # Find today's backup
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    backups = sorted(backup_dir.iterdir(), reverse=True) if backup_dir.exists() else []
    today_backup = None
    for b in backups:
        if b.is_dir() and today_str in b.name:
            today_backup = b
            break

    if not today_backup:
        log.warning("backup_verify: no backup found for %s", today_str)
        await _audit_end("backup_verify", int((time.monotonic() - t0) * 1000), "error=no_backup_today")
        return

    # Verify backup is non-empty
    db_files = list(today_backup.glob("*.db"))
    if not db_files:
        log.warning("backup_verify: backup %s contains no .db files", today_backup.name)
        await _audit_end("backup_verify", int((time.monotonic() - t0) * 1000), "error=empty_backup")
        return

    # Test integrity on a copy
    errors = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for db_file in db_files:
            try:
                test_path = Path(tmpdir) / db_file.name
                shutil.copy2(db_file, test_path)
                conn = sqlite3.connect(str(test_path))
                result = conn.execute("PRAGMA integrity_check").fetchone()[0]
                conn.close()
                if result != "ok":
                    errors.append(f"{db_file.name}: integrity={result}")
            except Exception as exc:
                errors.append(f"{db_file.name}: {exc}")

    # Calculate backup size
    backup_size = sum(f.stat().st_size for f in today_backup.rglob("*") if f.is_file())
    backup_mb = backup_size / (1024 * 1024)

    # ── Retention policy: 7 daily, 4 weekly (Sun), 3 monthly (1st) ──
    all_backups = sorted(
        [d for d in backup_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )
    keep = set()
    daily_count = weekly_count = monthly_count = 0
    for b in all_backups:
        # Parse date from backup name (expects YYYY-MM-DD somewhere in name)
        date_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", b.name)
        if not date_match:
            continue
        year, month, day = int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))
        try:
            backup_date = datetime(year, month, day)
        except ValueError:
            continue

        is_sunday = backup_date.weekday() == 6
        is_first = day == 1

        if daily_count < 7:
            keep.add(b)
            daily_count += 1
        if is_sunday and weekly_count < 4:
            keep.add(b)
            weekly_count += 1
        if is_first and monthly_count < 3:
            keep.add(b)
            monthly_count += 1

    pruned = 0
    for b in all_backups:
        if b not in keep:
            try:
                shutil.rmtree(b)
                pruned += 1
            except Exception as exc:
                log.warning("backup_verify: failed to prune %s: %s", b.name, exc)

    duration_ms = int((time.monotonic() - t0) * 1000)
    status = "ok" if not errors else f"errors={len(errors)}"
    log.info(
        "backup_verify_job: %s in %dms — size=%.1fMB files=%d pruned=%d",
        status, duration_ms, backup_mb, len(db_files), pruned,
    )
    await _audit_end(
        "backup_verify", duration_ms,
        f"{status} size={backup_mb:.1f}MB files={len(db_files)} pruned={pruned}",
    )
