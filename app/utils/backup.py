"""
Backup utility — nightly backup of critical data during dream mode.

Copies SQLite databases (with WAL checkpoint), ChromaDB directory, and
session JSON to a timestamped directory under settings.backup_path.
Retains the last 7 backups; older ones are pruned automatically.
"""
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

_MAX_BACKUPS = 7


def _checkpoint_sqlite(db_path: Path) -> None:
    """Force a WAL checkpoint before copying so the backup is consistent."""
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception as exc:
        log.warning("WAL checkpoint failed for %s: %s", db_path.name, exc)


def run_backup() -> dict:
    """
    Perform a full backup. Returns summary dict with counts and paths.

    Called by the scheduler during dream mode. Safe to call manually.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_root = Path(settings.backup_path)
    dest = backup_root / ts
    dest.mkdir(parents=True, exist_ok=True)

    copied = []
    errors = []

    # 1. SQLite databases
    db_dir = Path(settings.audit_log_path).parent
    for db_name in ["cdcn_audit.db", "indexer.db", "tracker.db", "users.db"]:
        db_path = db_dir / db_name
        if db_path.exists():
            try:
                _checkpoint_sqlite(db_path)
                shutil.copy2(str(db_path), str(dest / db_name))
                copied.append(db_name)
            except Exception as exc:
                log.error("Backup failed for %s: %s", db_name, exc)
                errors.append(f"{db_name}: {exc}")

    # 2. ChromaDB directory
    chroma_dir = Path(settings.chroma_path)
    if chroma_dir.exists():
        try:
            shutil.copytree(str(chroma_dir), str(dest / "chroma"), dirs_exist_ok=True)
            copied.append("chroma/")
        except Exception as exc:
            log.error("Backup failed for chroma: %s", exc)
            errors.append(f"chroma: {exc}")

    # 3. Skills config (identity files, templates)
    config_dir = Path("skills_config")
    if config_dir.exists():
        try:
            shutil.copytree(str(config_dir), str(dest / "skills_config"), dirs_exist_ok=True)
            copied.append("skills_config/")
        except Exception as exc:
            log.error("Backup failed for skills_config: %s", exc)
            errors.append(f"skills_config: {exc}")

    # 4. Users database (may be in auth dir)
    for alt_path in [Path("data/users.db"), Path(settings.audit_log_path).parent / "users.db"]:
        if alt_path.exists() and alt_path.name not in [c for c in copied if not c.endswith("/")]:
            try:
                _checkpoint_sqlite(alt_path)
                shutil.copy2(str(alt_path), str(dest / "users.db"))
                copied.append("users.db")
            except Exception as exc:
                errors.append(f"users.db: {exc}")
            break

    # Prune old backups
    pruned = 0
    existing = sorted(
        [d for d in backup_root.iterdir() if d.is_dir() and d.name != ts],
        key=lambda p: p.name,
    )
    while len(existing) >= _MAX_BACKUPS:
        oldest = existing.pop(0)
        try:
            shutil.rmtree(str(oldest))
            pruned += 1
        except Exception as exc:
            log.warning("Failed to prune old backup %s: %s", oldest.name, exc)

    summary = {
        "timestamp": ts,
        "destination": str(dest),
        "copied": copied,
        "errors": errors,
        "pruned": pruned,
    }
    log.info(
        "Backup complete: %d items copied, %d errors, %d old backups pruned",
        len(copied), len(errors), pruned,
    )
    return summary
