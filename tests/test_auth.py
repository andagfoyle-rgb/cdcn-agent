"""
Tests for app/auth/auth.py and audit_log append-only constraint.
"""
from __future__ import annotations

import time
import pytest

from app.auth.auth import (
    User,
    ROLE_PERMISSIONS,
    check_rate_limit,
    create_access_token,
    create_user,
    get_user,
    authenticate_user,
    has_permission,
    hash_password,
    verify_password,
    verify_token,
)
from jose import jwt


# ── Helpers ────────────────────────────────────────────────────────────────────


def _tmp_user_db(tmp_path, monkeypatch):
    """Point users.db and audit_log_path to a temp directory."""
    import app.config as cfg_mod
    fake_audit = str(tmp_path / "audit.db")
    monkeypatch.setattr(cfg_mod.settings, "audit_log_path", fake_audit)


# ── Password hashing ───────────────────────────────────────────────────────────


def test_hash_and_verify():
    h = hash_password("secret123")
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


def test_hash_is_bcrypt():
    h = hash_password("test")
    assert h.startswith("$2")


# ── User creation and retrieval ────────────────────────────────────────────────


def test_create_user(tmp_path, monkeypatch):
    _tmp_user_db(tmp_path, monkeypatch)
    user = create_user("alice", "secure_Pass1!", role="staff")
    assert user.username == "alice"
    assert user.role == "staff"
    assert user.active is True


def test_create_user_invalid_role(tmp_path, monkeypatch):
    _tmp_user_db(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Invalid role"):
        create_user("bob", "secure_Pass1!", role="superadmin")


def test_create_user_duplicate(tmp_path, monkeypatch):
    _tmp_user_db(tmp_path, monkeypatch)
    create_user("carol", "secure_Pass1!", role="trustee")
    with pytest.raises(ValueError, match="already exists"):
        create_user("carol", "secure_Pass2!", role="staff")


def test_get_user_returns_none_for_unknown(tmp_path, monkeypatch):
    _tmp_user_db(tmp_path, monkeypatch)
    # Disable legacy admin fallback
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "admin_username", "__nobody__")
    monkeypatch.setattr(cfg_mod.settings, "admin_password_hash", "")
    assert get_user("nobody") is None


def test_get_user_legacy_fallback(tmp_path, monkeypatch):
    _tmp_user_db(tmp_path, monkeypatch)
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "admin_username", "legacyadmin")
    monkeypatch.setattr(
        cfg_mod.settings, "admin_password_hash", hash_password("legacypass")
    )
    user = get_user("legacyadmin")
    assert user is not None
    assert user.role == "admin"


# ── authenticate_user ──────────────────────────────────────────────────────────


def test_authenticate_user_success(tmp_path, monkeypatch):
    _tmp_user_db(tmp_path, monkeypatch)
    create_user("dave", "correct_Pass1!", role="staff")
    user = authenticate_user("dave", "correct_Pass1!")
    assert isinstance(user, User)
    assert user.username == "dave"


def test_authenticate_user_wrong_password(tmp_path, monkeypatch):
    _tmp_user_db(tmp_path, monkeypatch)
    create_user("eve", "correct_Pass1!", role="staff")
    assert authenticate_user("eve", "wrongpassword") is None


def test_authenticate_user_unknown(tmp_path, monkeypatch):
    _tmp_user_db(tmp_path, monkeypatch)
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "admin_username", "__nobody__")
    monkeypatch.setattr(cfg_mod.settings, "admin_password_hash", "")
    assert authenticate_user("ghost", "pass") is None


# ── JWT ────────────────────────────────────────────────────────────────────────


def test_create_access_token_has_sub_and_role():
    from app.config import settings
    user = User(username="frank", hashed_password="x", role="admin")
    token = create_access_token(user)
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    assert payload["sub"] == "frank"
    assert payload["role"] == "admin"
    assert "exp" in payload


def test_create_access_token_8h_expiry():
    from app.config import settings
    user = User(username="grace", hashed_password="x", role="staff")
    token = create_access_token(user)
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    exp = payload["exp"]
    now = time.time()
    # Should expire between 7.9 h and 8.1 h from now
    assert 7.9 * 3600 < (exp - now) < 8.1 * 3600


def test_verify_token_valid():
    user = User(username="henry", hashed_password="x", role="trustee")
    token = create_access_token(user)
    result = verify_token(token)
    assert result is not None
    assert result.username == "henry"
    assert result.role == "trustee"


def test_verify_token_invalid():
    assert verify_token("not.a.token") is None


def test_verify_token_tampered():
    import app.config as cfg_mod
    user = User(username="ivan", hashed_password="x", role="staff")
    token = create_access_token(user)
    # Flip a character in the signature
    tampered = token[:-4] + ("XXXX" if not token.endswith("XXXX") else "YYYY")
    assert verify_token(tampered) is None


# ── Role permissions ───────────────────────────────────────────────────────────


def test_trustee_can_search():
    assert has_permission("trustee", "search")


def test_trustee_cannot_approve():
    assert not has_permission("trustee", "approve_changes")


def test_staff_can_index():
    assert has_permission("staff", "index")


def test_staff_cannot_add_users():
    assert not has_permission("staff", "add_users")


def test_admin_has_all_permissions():
    admin_perms = ROLE_PERMISSIONS["admin"]
    for perm in ("search", "index", "approve_changes", "add_users",
                 "view_audit_log", "trigger_heartbeat", "delete_documents"):
        assert perm in admin_perms


def test_unknown_role_has_no_permissions():
    assert not has_permission("superuser", "search")


# ── Rate limiting ──────────────────────────────────────────────────────────────


def test_rate_limit_allows_up_to_20():
    # Use a unique user_id so bucket is fresh
    uid = f"test_rl_{time.monotonic()}"
    for _ in range(20):
        assert check_rate_limit(uid), "Should be allowed within first 20"


def test_rate_limit_blocks_at_21():
    uid = f"test_rl_block_{time.monotonic()}"
    for _ in range(20):
        check_rate_limit(uid)
    assert not check_rate_limit(uid), "21st request should be rate-limited"


# ── Audit log append-only constraint ──────────────────────────────────────────


def test_audit_log_no_mutations_in_source():
    """
    Verify the audit_log module source contains no UPDATE or DELETE statements
    that target audit tables.  This is the static code-level guarantee of the
    append-only constraint.
    """
    import pathlib
    src = pathlib.Path("app/storage/audit_log.py").read_text()

    # Should never appear as SQL keywords mutating log rows
    assert "UPDATE audit_log" not in src.upper()
    assert "UPDATE llm_calls" not in src.upper()
    assert "UPDATE document_ops" not in src.upper()
    assert "UPDATE auth_events" not in src.upper()
    assert "UPDATE state_transitions" not in src.upper()
    assert "UPDATE dream_cycles" not in src.upper()
    assert "UPDATE pending_change_actions" not in src.upper()
    assert "DELETE FROM audit_log" not in src.upper()
    assert "DELETE FROM llm_calls" not in src.upper()
    assert "DELETE FROM pending_change_actions" not in src.upper()


@pytest.mark.asyncio
async def test_audit_log_event_is_insert_only(tmp_path, monkeypatch):
    """log_event writes exactly one new row; calling it twice gives two rows."""
    import app.config as cfg_mod
    monkeypatch.setattr(
        cfg_mod.settings, "audit_log_path", str(tmp_path / "audit.db")
    )
    from app.storage.audit_log import log_event, recent_events

    await log_event("user1", "test_action", "target", "detail")
    await log_event("user2", "test_action2")
    rows = await recent_events(limit=10)
    assert len(rows) == 2
    actors = {r["actor"] for r in rows}
    assert "user1" in actors
    assert "user2" in actors
