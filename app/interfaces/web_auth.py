"""
Web authentication helpers — cookie-based JWT validation, presence tracking.

Used by web_routes.py and web_socket.py to authenticate requests.
"""
from __future__ import annotations

import time as _time
from typing import Optional

from fastapi import Request, WebSocket

from app.auth.auth import verify_token
from app.config import settings


# ── Online presence tracking (heartbeat-based) ────────────────────────────────
_online_sessions: dict[str, float] = {}   # username -> last_seen epoch
_PRESENCE_TTL = 90                         # seconds before considered offline


def touch_presence(username: str) -> None:
    """Record that a user is currently active."""
    _online_sessions[username] = _time.monotonic()


def get_online_users() -> list[str]:
    """Return usernames seen within the last _PRESENCE_TTL seconds."""
    now = _time.monotonic()
    cutoff = now - _PRESENCE_TTL
    return [u for u, t in list(_online_sessions.items()) if t >= cutoff]


# ── Auth cookie helpers ────────────────────────────────────────────────────────

def get_user_from_cookie(request_or_ws) -> str | None:
    """Validate the access_token cookie and return the username, or None."""
    token = request_or_ws.cookies.get("access_token")
    if not token:
        return None
    user = verify_token(token)
    return user.username if user else None


def get_full_user_from_cookie(request_or_ws):
    """Validate the access_token cookie and return the full User object, or None."""
    token = request_or_ws.cookies.get("access_token")
    if not token:
        return None
    return verify_token(token)


# ── Upload validation ─────────────────────────────────────────────────────────

ALLOWED_EXTS = {
    ".pdf", ".docx", ".txt", ".md",
    ".xlsx", ".csv", ".odt", ".pptx",
    ".jpg", ".jpeg", ".png", ".gif",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_MESSAGE_LENGTH = 10_000  # max chat message length (characters)

ALLOWED_MIMES = {
    "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain", "text/markdown", "text/csv", "text/x-csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "image/jpeg", "image/png", "image/gif",
    "application/octet-stream",  # fallback for some doc types
}


def validate_upload_mime(content: bytes, filename: str) -> bool:
    """Validate file content type via magic bytes. Rejects executables and scripts."""
    try:
        import magic
        detected = magic.from_buffer(content[:8192], mime=True)
    except Exception:
        return True  # fail open if magic not available
    blocked_prefixes = ("application/x-executable", "application/x-mach", "application/x-sh",
                        "text/x-python", "text/x-perl", "text/x-shellscript",
                        "application/x-dosexec", "application/x-elf")
    if any(detected.startswith(p) for p in blocked_prefixes):
        return False
    return True


# ── Archive path safety ───────────────────────────────────────────────────────

from pathlib import Path
import os


def archive_root() -> Path:
    """Return the resolved archive root directory."""
    return Path(settings.watched_folder).resolve()


def safe_archive_path(user_path: str) -> Path | None:
    """
    Resolve user_path relative to the archive root, ensuring it stays within it.
    Returns None if the path would escape the archive root.
    """
    root = archive_root()
    try:
        candidate = (root / user_path.lstrip("/")).resolve()
        if candidate == root or str(candidate).startswith(str(root) + os.sep):
            return candidate
        return None
    except Exception:
        return None


# ── Active WebSocket connections ──────────────────────────────────────────────
active_ws: dict[str, "WebSocket"] = {}
