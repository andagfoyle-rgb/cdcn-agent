"""Section 5 — Authentication System simulation."""
import tempfile, os
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    import app.config as cfg
    cfg.settings.audit_log_path = str(Path(tmp) / 'audit.db')
    cfg.settings.secret_key = 'a' * 64

    from app.auth.auth import (
        hash_password, verify_password, create_user, authenticate_user,
        create_access_token, verify_token, check_rate_limit, has_permission,
        User, ROLE_PERMISSIONS
    )

    # 1. Password hashing
    h = hash_password('secret123')
    assert h.startswith('$2'), f'Not bcrypt: {h[:10]}'
    assert verify_password('secret123', h), 'Verify failed'
    assert not verify_password('wrong', h), 'Wrong password accepted'
    print('Password hashing: PASSED')

    # 2. User creation
    user = create_user('alice', 'pass123', role='staff')
    assert user.username == 'alice'
    assert user.role == 'staff'
    print(f'User created: {user.username} / {user.role}')

    # 3. Authentication
    auth_user = authenticate_user('alice', 'pass123')
    assert auth_user is not None
    assert auth_user.username == 'alice'

    bad = authenticate_user('alice', 'wrongpass')
    assert bad is None
    print('Authentication: PASSED')

    # 4. JWT
    token = create_access_token(auth_user)
    assert isinstance(token, str) and len(token) > 20
    decoded = verify_token(token)
    assert decoded is not None
    assert decoded.username == 'alice'
    assert decoded.role == 'staff'
    print(f'JWT: created and verified OK (user={decoded.username})')

    # 5. Invalid token
    assert verify_token('invalid.token.here') is None
    print('Invalid token rejected: PASSED')

    # 6. Role permissions
    assert has_permission('admin', 'approve_changes')
    assert has_permission('staff', 'index')
    assert not has_permission('trustee', 'approve_changes')
    assert not has_permission('trustee', 'add_users')
    print('Role permissions: PASSED')

    # 7. Rate limiting
    uid = 'test_user_rl'
    for i in range(20):
        assert check_rate_limit(uid), f'Should be allowed at request {i+1}'
    assert not check_rate_limit(uid), '21st should be rate-limited'
    print('Rate limiting (20 req/min): PASSED')

    print('\nSECTION 5 Authentication: ALL PASSED')
