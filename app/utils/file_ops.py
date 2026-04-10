"""File system resilience utilities for CDCN Agent."""
import logging
import os
import shutil
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def safe_read(path: Path) -> str:
    """Read a file with existence and permission checks. Returns empty string on error."""
    if not path.exists():
        log.warning("File not found: %s", path)
        return ""
    try:
        return path.read_text(errors="replace")
    except PermissionError:
        log.warning("Permission denied reading %s", path)
        return ""
    except OSError as exc:
        log.warning("OS error reading %s: %s", path, exc)
        return ""


def atomic_write(path: Path, content: str) -> bool:
    """Write content to a temp file, then atomically rename. Returns True on success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp_path, str(path))
            return True
        except Exception:
            os.unlink(tmp_path)
            raise
    except OSError as exc:
        log.warning("Atomic write failed for %s: %s", path, exc)
        return False


def check_disk_space(path: Path, min_bytes: int = 100 * 1024 * 1024) -> bool:
    """Check if at least min_bytes are available. Returns True if sufficient."""
    try:
        usage = shutil.disk_usage(str(path))
        if usage.free < min_bytes:
            log.warning("Low disk space: %d MB free at %s", usage.free // (1024 * 1024), path)
            return False
        return True
    except OSError:
        return True  # fail open
