"""
User authentication and authorisation for CDCN Agent.

Users are stored in a SQLite table (users.db, beside the audit log).
Passwords are bcrypt-hashed.  JWTs embed sub (username) and role, valid 8 h.

Roles and permissions
─────────────────────
  trustee  read-only: search, query, read_pending
  staff    + trigger indexing, write_skill
  admin    + add_users, delete_documents, approve_changes,
             view_audit_log, trigger_heartbeat

Rate limiting
─────────────
  In-memory token bucket: 20 requests per user per minute.
  Call check_rate_limit(user_id) in the agentic loop before any LLM call.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

import re as _re

import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 8

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)

# ── Password complexity ──────────────────────────────────────────────────────

import re as _re

_PASSWORD_MIN_LENGTH = 8
_PASSWORD_PATTERN = _re.compile(r"[0-9!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]")


def validate_password_strength(password: str) -> str | None:
    """Return an error message if the password is too weak, or None if acceptable."""
    if len(password) < _PASSWORD_MIN_LENGTH:
        return f"Password must be at least {_PASSWORD_MIN_LENGTH} characters."
    if not _PASSWORD_PATTERN.search(password):
        return "Password must contain at least one number or symbol."
    return None


# ── Login lockout (5 attempts, 15-minute cooldown) ───────────────────────────

_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECS = 15 * 60  # 15 minutes


@dataclass
class _LoginTracker:
    failures: int = 0
    locked_until: float = 0.0


_login_trackers: dict[str, _LoginTracker] = {}
_login_lock = Lock()


def _check_login_lockout(username: str) -> str | None:
    """Return an error message if the account is locked out, or None if OK."""
    now = time.monotonic()
    with _login_lock:
        tracker = _login_trackers.get(username)
        if tracker and tracker.locked_until > now:
            remaining = int(tracker.locked_until - now)
            mins = remaining // 60 + 1
            return f"Account temporarily locked. Try again in {mins} minute(s)."
    return None


def _record_login_failure(username: str) -> None:
    """Record a failed login attempt; lock out after threshold."""
    now = time.monotonic()
    with _login_lock:
        tracker = _login_trackers.setdefault(username, _LoginTracker())
        tracker.failures += 1
        if tracker.failures >= _LOGIN_MAX_ATTEMPTS:
            tracker.locked_until = now + _LOGIN_LOCKOUT_SECS


def _clear_login_failures(username: str) -> None:
    """Clear failed login attempts on successful login."""
    with _login_lock:
        _login_trackers.pop(username, None)


# ── Password helpers (direct bcrypt — avoids passlib/bcrypt 5.x compat issue) ─

def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def validate_password(password: str) -> str | None:
    """Return error message if password doesn't meet policy, or None if valid."""
    return validate_password_strength(password)


# ── User model ─────────────────────────────────────────────────────────────────


@dataclass
class User:
    username: str
    hashed_password: str
    role: str        # "admin" | "staff" | "trustee"
    active: bool = True


# ── Role permissions ───────────────────────────────────────────────────────────

_DEFAULT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "trustee": frozenset({"search", "query", "read_pending",
                          "individual_chat", "group_chat"}),
    "staff":   frozenset({"search", "query", "read_pending", "index", "write_skill",
                          "individual_chat", "group_chat"}),
    "admin":   frozenset({
        "search", "query", "read_pending", "index", "write_skill",
        "add_users", "delete_documents", "approve_changes",
        "view_audit_log", "trigger_heartbeat",
        "individual_chat", "group_chat",
    }),
}

# All known permissions (used for the roles UI checkboxes)
ALL_PERMISSIONS: tuple[str, ...] = (
    "search", "query", "read_pending", "index", "write_skill",
    "add_users", "delete_documents", "approve_changes",
    "view_audit_log", "trigger_heartbeat",
    "individual_chat", "group_chat",
)

# Mutable cache loaded from DB — refreshed by _load_roles_from_db()
ROLE_PERMISSIONS: dict[str, frozenset[str]] = dict(_DEFAULT_ROLE_PERMISSIONS)


def has_permission(role: str, permission: str) -> bool:
    """Return True if the role grants the named permission."""
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


# ── SQLite user store ──────────────────────────────────────────────────────────

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    username        TEXT PRIMARY KEY,
    hashed_password TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'staff',
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);
"""


def _user_db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return db_path
    return str(Path(settings.audit_log_path).parent / "users.db")


def _open_user_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = _user_db_path(db_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_USERS)
    conn.commit()
    return conn


def create_user(
    username: str, password: str, role: str = "staff", db_path: Optional[str] = None
) -> User:
    """Create and persist a new user.  Raises ValueError on bad role or duplicate."""
    if role not in ROLE_PERMISSIONS:
        raise ValueError(
            f"Invalid role '{role}'. Choose from: {', '.join(ROLE_PERMISSIONS)}"
        )
    err = validate_password(password)
    if err:
        raise ValueError(err)
    hashed = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()
    with _open_user_db(db_path) as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, hashed_password, role, active, created_at)"
                " VALUES (?, ?, ?, 1, ?)",
                (username, hashed, role, now),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"User already exists: {username}")
    return User(username=username, hashed_password=hashed, role=role, active=True)


def get_user(username: str, db_path: Optional[str] = None) -> Optional[User]:
    """Return User from SQLite, falling back to the .env admin account, or None."""
    with _open_user_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    if row:
        return User(
            username=row["username"],
            hashed_password=row["hashed_password"],
            role=row["role"],
            active=bool(row["active"]),
        )
    # Legacy fallback: admin configured via ADMIN_USERNAME / ADMIN_PASSWORD_HASH
    if username == settings.admin_username and settings.admin_password_hash:
        return User(
            username=settings.admin_username,
            hashed_password=settings.admin_password_hash,
            role="admin",
            active=True,
        )
    return None


def authenticate_user(
    username: str, password: str, db_path: Optional[str] = None
) -> Optional[User]:
    """Return User on success, None on wrong credentials or inactive account."""
    if not check_login_allowed(username):
        return None
    user = get_user(username, db_path)
    if not user or not user.active:
        record_login_attempt(username, False)
        return None
    if not verify_password(password, user.hashed_password):
        record_login_attempt(username, False)
        return None
    record_login_attempt(username, True)
    return user


# ── JWT ────────────────────────────────────────────────────────────────────────


def create_access_token(user: User) -> str:
    """Create a signed JWT embedding username and role, valid for 8 hours."""
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": user.username, "role": user.role, "exp": expire},
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def verify_token(token: str) -> Optional[User]:
    """Decode JWT and return a User stub, or None if invalid or expired."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username: str = payload.get("sub", "")
        role: str = payload.get("role", "")
        if not username or not role:
            return None
        return User(username=username, hashed_password="", role=role, active=True)
    except JWTError:
        return None


# ── Rate limiting (token bucket, 20 req / user / minute) ──────────────────────

_RATE_LIMIT = 20
_REFILL_RATE = 20 / 60.0   # tokens per second


@dataclass
class _Bucket:
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


_rl_buckets: dict[str, _Bucket] = {}
_rl_lock = Lock()


def check_rate_limit(user_id: str) -> bool:
    """
    Consume one token from the user's bucket.
    Returns True if the request is allowed, False if the bucket is empty.
    """
    now = time.monotonic()
    with _rl_lock:
        bucket = _rl_buckets.setdefault(
            user_id, _Bucket(tokens=float(_RATE_LIMIT), last_refill=now)
        )
        elapsed = now - bucket.last_refill
        bucket.tokens = min(float(_RATE_LIMIT), bucket.tokens + elapsed * _REFILL_RATE)
        bucket.last_refill = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        return False


# ── Login lockout public API (delegates to primary implementation above) ──────


def check_login_allowed(username: str) -> bool:
    """Return True if the user is allowed to attempt login."""
    return _check_login_lockout(username) is None


def record_login_attempt(username: str, success: bool) -> None:
    """Record a login attempt. Lock out after too many failures."""
    if success:
        _clear_login_failures(username)
    else:
        _record_login_failure(username)


# ── FastAPI dependencies ───────────────────────────────────────────────────────


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    request: Request = None,
) -> User:
    """
    Resolve the current user from a Bearer token (API clients) or the
    httponly 'access_token' cookie (web UI).  Raises HTTP 401 if neither
    is present or the token is invalid.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    resolved = token
    if not resolved and request is not None:
        resolved = request.cookies.get("access_token")
    if not resolved:
        raise credentials_exc
    user = verify_token(resolved)
    if user is None:
        raise credentials_exc
    return user


def require_permission(permission: str):
    """Dependency factory — raises HTTP 403 if the user lacks the named permission."""
    async def _dep(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' does not have '{permission}' permission.",
            )
        return user
    return _dep




# ── Registration and approval ──────────────────────────────────────────────────

def register_user(
    username: str,
    password: str,
    display_name: str,
    email: str,
    requested_role: str = "staff",
    reason: str = "",
    db_path: Optional[str] = None,
) -> dict:
    """Submit a registration request. Returns dict with id and status."""
    if requested_role not in ROLE_PERMISSIONS:
        raise ValueError(f"Invalid role '{requested_role}'.")
    err = validate_password(password)
    if err:
        raise ValueError(err)
    hashed = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()
    db = _user_db_path(db_path)
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_USERS)
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO registration_requests"
            " (username, display_name, email, hashed_password, requested_role, reason, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (username, display_name, email, hashed, requested_role, reason, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"Username '{username}' already has a pending request.")
    conn.close()
    return {"username": username, "status": "pending"}


def get_pending_registrations(db_path: Optional[str] = None) -> list[dict]:
    """Return all pending registration requests."""
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM registration_requests WHERE status = 'pending' ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def approve_registration(
    request_id: int,
    approved_by: str,
    role: Optional[str] = None,
    db_path: Optional[str] = None,
) -> User:
    """Approve a registration request and create the user account."""
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_USERS)
    conn.commit()
    row = conn.execute(
        "SELECT * FROM registration_requests WHERE id = ? AND status = 'pending'",
        (request_id,),
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"No pending request with id {request_id}")
    final_role = role or row["requested_role"]
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "INSERT INTO users (username, hashed_password, role, active, created_at,"
            " email, display_name, status, requested_role)"
            " VALUES (?, ?, ?, 1, ?, ?, ?, 'active', ?)",
            (row["username"], row["hashed_password"], final_role, now,
             row["email"], row["display_name"], final_role),
        )
    except sqlite3.IntegrityError:
        conn.execute(
            "UPDATE users SET active = 1, role = ? WHERE username = ?",
            (final_role, row["username"]),
        )
    conn.execute(
        "UPDATE registration_requests SET status = 'approved', reviewed_at = ?, reviewed_by = ?"
        " WHERE id = ?",
        (now, approved_by, request_id),
    )
    conn.commit()
    conn.close()
    return User(username=row["username"], hashed_password=row["hashed_password"],
                role=final_role, active=True)


def reject_registration(
    request_id: int,
    rejected_by: str,
    reason: str = "",
    db_path: Optional[str] = None,
) -> None:
    """Reject a registration request."""
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE registration_requests SET status = 'rejected', reviewed_at = ?,"
        " reviewed_by = ?, rejection_reason = ? WHERE id = ? AND status = 'pending'",
        (now, rejected_by, reason, request_id),
    )
    conn.commit()
    conn.close()


def list_users(db_path: Optional[str] = None) -> list[dict]:
    """Return all users."""
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_USERS)
    conn.commit()
    rows = conn.execute(
        "SELECT username, role, active, created_at, email, display_name, status"
        " FROM users ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user_role(
    username: str, new_role: str, db_path: Optional[str] = None
) -> None:
    """Change a user's role."""
    if new_role not in ROLE_PERMISSIONS:
        raise ValueError(f"Invalid role '{new_role}'.")
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET role = ? WHERE username = ?", (new_role, username))
    conn.commit()
    conn.close()


def suspend_user(username: str, db_path: Optional[str] = None) -> None:
    """Suspend a user account."""
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET active = 0 WHERE username = ?", (username,))
    conn.commit()
    conn.close()


def reactivate_user(username: str, db_path: Optional[str] = None) -> None:
    """Reactivate a suspended user account."""
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET active = 1 WHERE username = ?", (username,))
    conn.commit()
    conn.close()


def update_user_info(
    username: str,
    *,
    display_name: Optional[str] = None,
    email: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Update a user's display name and/or email."""
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    if display_name is not None:
        conn.execute("UPDATE users SET display_name = ? WHERE username = ?",
                     (display_name, username))
    if email is not None:
        conn.execute("UPDATE users SET email = ? WHERE username = ?",
                     (email, username))
    conn.commit()
    conn.close()


def reset_user_password(
    username: str, new_password: str, db_path: Optional[str] = None
) -> None:
    """Reset a user's password (admin action)."""
    hashed = hash_password(new_password)
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET hashed_password = ? WHERE username = ?",
                 (hashed, username))
    conn.commit()
    conn.close()


# ── Roles table ───────────────────────────────────────────────────────────────

_CREATE_ROLES = """
CREATE TABLE IF NOT EXISTS roles (
    name        TEXT PRIMARY KEY,
    permissions TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    is_system   INTEGER NOT NULL DEFAULT 0
);
"""


def _ensure_roles_table(db_path: Optional[str] = None) -> None:
    """Create the roles table and seed defaults if empty."""
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    conn.execute(_CREATE_ROLES)
    conn.commit()
    # Seed defaults if table is empty
    count = conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
    if count == 0:
        for role_name, perms in _DEFAULT_ROLE_PERMISSIONS.items():
            conn.execute(
                "INSERT INTO roles (name, permissions, description, is_system) "
                "VALUES (?, ?, ?, ?)",
                (role_name, ",".join(sorted(perms)),
                 f"Default {role_name} role", 1),
            )
        conn.commit()
    conn.close()


def _load_roles_from_db(db_path: Optional[str] = None) -> None:
    """Load roles from DB into the in-memory ROLE_PERMISSIONS dict."""
    _ensure_roles_table(db_path)
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT name, permissions FROM roles").fetchall()
    conn.close()
    ROLE_PERMISSIONS.clear()
    for row in rows:
        perms = frozenset(p.strip() for p in row["permissions"].split(",") if p.strip())
        ROLE_PERMISSIONS[row["name"]] = perms


def list_roles(db_path: Optional[str] = None) -> list[dict]:
    """Return all roles with their permissions."""
    _ensure_roles_table(db_path)
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT name, permissions, description, is_system FROM roles ORDER BY name"
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        perms = [p.strip() for p in r["permissions"].split(",") if p.strip()]
        result.append({
            "name": r["name"],
            "permissions": perms,
            "description": r["description"],
            "is_system": bool(r["is_system"]),
        })
    return result


def create_role(
    name: str,
    permissions: list[str],
    description: str = "",
    db_path: Optional[str] = None,
) -> None:
    """Create a new role."""
    _ensure_roles_table(db_path)
    name = name.strip().lower()
    if not name:
        raise ValueError("Role name cannot be empty.")
    # Validate permissions
    invalid = set(permissions) - set(ALL_PERMISSIONS)
    if invalid:
        raise ValueError(f"Invalid permissions: {', '.join(invalid)}")
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO roles (name, permissions, description, is_system) "
            "VALUES (?, ?, ?, 0)",
            (name, ",".join(sorted(permissions)), description),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"Role '{name}' already exists.")
    conn.close()
    _load_roles_from_db(db_path)


def update_role(
    name: str,
    permissions: list[str],
    description: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Update an existing role's permissions and/or description."""
    _ensure_roles_table(db_path)
    invalid = set(permissions) - set(ALL_PERMISSIONS)
    if invalid:
        raise ValueError(f"Invalid permissions: {', '.join(invalid)}")
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    if description is not None:
        conn.execute(
            "UPDATE roles SET permissions = ?, description = ? WHERE name = ?",
            (",".join(sorted(permissions)), description, name),
        )
    else:
        conn.execute(
            "UPDATE roles SET permissions = ? WHERE name = ?",
            (",".join(sorted(permissions)), name),
        )
    conn.commit()
    conn.close()
    _load_roles_from_db(db_path)


def delete_role(name: str, db_path: Optional[str] = None) -> None:
    """Delete a role. Cannot delete system roles or roles assigned to users."""
    _ensure_roles_table(db_path)
    db = _user_db_path(db_path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT is_system FROM roles WHERE name = ?", (name,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Role '{name}' does not exist.")
    if row["is_system"]:
        conn.close()
        raise ValueError(f"Cannot delete system role '{name}'.")
    # Check no users are assigned this role
    user_count = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = ?", (name,)
    ).fetchone()[0]
    if user_count > 0:
        conn.close()
        raise ValueError(
            f"Cannot delete role '{name}' — {user_count} user(s) still assigned to it."
        )
    conn.execute("DELETE FROM roles WHERE name = ?", (name,))
    conn.commit()
    conn.close()
    _load_roles_from_db(db_path)


# ── Load roles from DB on import ──────────────────────────────────────────────
try:
    _load_roles_from_db()
except Exception:
    pass  # DB may not exist yet at import time; defaults remain
