"""
Shared test fixtures for CDCN Agent test suite.

Provides temporary databases, mock LLM, sample documents, and test client.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Environment setup (before any app imports) ────────────────────────────────
# Point settings at temp paths so tests never touch production data.

@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    """Redirect all storage paths to tmp_path for every test."""
    import app.config as cfg

    monkeypatch.setattr(cfg.settings, "audit_log_path", str(tmp_path / "audit.db"))
    monkeypatch.setattr(cfg.settings, "chroma_path", str(tmp_path / "chroma"))
    monkeypatch.setattr(cfg.settings, "memory_path", str(tmp_path / "memory"))
    monkeypatch.setattr(cfg.settings, "watched_folder", str(tmp_path / "documents"))
    monkeypatch.setattr(cfg.settings, "pending_changes_path", str(tmp_path / "pending"))
    monkeypatch.setattr(cfg.settings, "backup_path", str(tmp_path / "backups"))

    # Create directories
    for d in ("documents", "memory", "pending", "backups"):
        (tmp_path / d).mkdir(exist_ok=True)


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary SQLite database with the users schema."""
    db_path = str(tmp_path / "test_users.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username        TEXT PRIMARY KEY,
            hashed_password TEXT NOT NULL,
            role            TEXT NOT NULL DEFAULT 'staff',
            active          INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def mock_llm():
    """Mock LLM client that returns predictable responses and tracks calls."""
    client = AsyncMock()
    client.call_count = 0
    client.last_prompt = None

    async def _chat(messages, **kwargs):
        client.call_count += 1
        client.last_prompt = messages
        return "This is a mock LLM response about CDCN governance documents."

    async def _stream(messages, **kwargs):
        client.call_count += 1
        client.last_prompt = messages
        for word in "This is a mock streaming response.".split():
            yield word + " "

    client.chat = _chat
    client.stream = _stream
    client.embed = AsyncMock(return_value=[0.1] * 384)
    return client


@pytest.fixture
def sample_documents(tmp_path):
    """Create minimal sample test documents and return their paths."""
    docs_dir = tmp_path / "documents"
    docs_dir.mkdir(exist_ok=True)

    # Text document
    txt = docs_dir / "test_minutes.txt"
    txt.write_text(
        "CDCN Board Meeting Minutes - 10 February 2026\n\n"
        "Present: Alice Smith, Bob Jones, Carol Davis\n"
        "Apologies: Dave Wilson\n\n"
        "1. Welcome and Apologies\n"
        "The chair welcomed everyone to the meeting.\n\n"
        "2. Development Officer Funding\n"
        "The Development Officer reported on February 2026 funding applications.\n"
        "A grant of £5,000 was received from the Community Fund.\n\n"
        "3. AI Ethics Policy\n"
        "The board discussed the AI Ethics Policy on data handling.\n"
        "RESOLVED: To adopt the policy with amendments.\n"
    )

    # Markdown document
    md = docs_dir / "ai_ethics_policy.md"
    md.write_text(
        "# CDCN AI Ethics Policy\n\n"
        "## Data Handling\n"
        "All personal data must be stored securely and processed in accordance "
        "with GDPR. No data shall leave the CDCN network.\n\n"
        "## Transparency\n"
        "The AI agent shall always identify itself as an AI assistant.\n"
    )

    return {
        "dir": docs_dir,
        "minutes": txt,
        "policy": md,
    }


@pytest.fixture
def test_client():
    """FastAPI test client for API testing."""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def admin_token():
    """Create a valid admin JWT token for testing."""
    from app.auth.auth import User, create_access_token
    user = User(username="testadmin", hashed_password="x", role="admin")
    return create_access_token(user)


@pytest.fixture
def staff_token():
    """Create a valid staff JWT token for testing."""
    from app.auth.auth import User, create_access_token
    user = User(username="teststaff", hashed_password="x", role="staff")
    return create_access_token(user)


@pytest.fixture
def trustee_token():
    """Create a valid trustee JWT token for testing."""
    from app.auth.auth import User, create_access_token
    user = User(username="testtrustee", hashed_password="x", role="trustee")
    return create_access_token(user)
