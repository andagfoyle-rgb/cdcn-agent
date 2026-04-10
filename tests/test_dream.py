"""
Tests for DreamWorkerSkill.

DreamWorkerSkill requires constructor injection of all dependencies.
These tests pass lightweight mocks so no real LLM, Chroma, or filesystem
access is needed.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_dream_worker(tmp_path, monkeypatch=None):
    """Return a DreamWorkerSkill with all deps mocked."""
    import app.config as cfg_mod

    if monkeypatch:
        monkeypatch.setattr(cfg_mod.settings, "memory_path", str(tmp_path / "memory"))
        monkeypatch.setattr(cfg_mod.settings, "chroma_path", str(tmp_path / "chroma"))
        monkeypatch.setattr(
            cfg_mod.settings, "audit_log_path", str(tmp_path / "audit.db")
        )

    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)

    # Mock LLM client
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="Mock LLM response.")

    # Mock memory skill
    memory = MagicMock()
    memory.get_system_prompt = AsyncMock(return_value="System prompt.")
    memory.read_recent_journal = MagicMock(
        return_value=MagicMock(success=True, output="Journal entries.")
    )
    memory.read_current_task = MagicMock(
        return_value=MagicMock(success=True, output="Current task.")
    )
    memory.write_journal = MagicMock()
    memory.write_current_task = MagicMock()
    memory.log_session_summary = MagicMock()
    memory.update_memory = MagicMock()

    # Mock vector store
    vs = MagicMock()
    vs.list_documents = MagicMock(return_value=[])
    vs.search = AsyncMock(return_value=[])
    vs.get_all_document_metadata = MagicMock(return_value=[])
    vs._get_collection = MagicMock(return_value=MagicMock(get=MagicMock(
        return_value={"ids": [], "documents": [], "metadatas": []}
    )))

    # Mock session manager
    sm = MagicMock()
    sm.get_today_sessions = MagicMock(return_value=[])

    # Mock pending_changes module
    pc = MagicMock()
    pc.propose = MagicMock(return_value="abc12345")
    pc.list_pending = MagicMock(return_value=[])

    # Mock audit_log module
    al = MagicMock()
    al.log_event = AsyncMock()
    al.log_dream_cycle = AsyncMock()

    from app.skills.dream_worker import DreamWorkerSkill
    return DreamWorkerSkill(
        llm_client=llm,
        memory_skill=memory,
        vector_store=vs,
        session_manager=sm,
        pending_changes=pc,
        audit_log=al,
    )


# ── Constructor ────────────────────────────────────────────────────────────────


def test_dream_worker_constructs(tmp_path):
    dw = _make_dream_worker(tmp_path)
    assert dw is not None


# ── run_full_cycle on empty data ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_cycle_completes_on_empty_data(tmp_path, monkeypatch):
    dw = _make_dream_worker(tmp_path, monkeypatch)

    with patch.object(dw, "_dream_call", new=AsyncMock(return_value="LLM ok.")):
        await dw.run_full_cycle()
    # Should complete without raising


@pytest.mark.asyncio
async def test_full_cycle_all_five_tasks_attempted(tmp_path, monkeypatch):
    """Each of the five tasks should be called exactly once."""
    dw = _make_dream_worker(tmp_path, monkeypatch)

    task_calls: list[str] = []

    def _mock_task(name):
        task_calls.append(name)

    with patch.object(dw, "consolidate_memory", new=AsyncMock(side_effect=lambda: _mock_task("consolidate_memory"))), \
         patch.object(dw, "self_critique",      new=AsyncMock(side_effect=lambda: _mock_task("self_critique"))), \
         patch.object(dw, "map_document_relationships", new=AsyncMock(side_effect=lambda: _mock_task("map_document_relationships"))), \
         patch.object(dw, "anticipate_tomorrow",        new=AsyncMock(side_effect=lambda: _mock_task("anticipate_tomorrow"))), \
         patch.object(dw, "refine_style_guide",         new=AsyncMock(side_effect=lambda: _mock_task("refine_style_guide"))):
        await dw.run_full_cycle()

    assert set(task_calls) == {
        "consolidate_memory",
        "self_critique",
        "map_document_relationships",
        "anticipate_tomorrow",
        "refine_style_guide",
    }


@pytest.mark.asyncio
async def test_full_cycle_continues_after_task_failure(tmp_path, monkeypatch):
    """A failure in one task must not abort subsequent tasks."""
    dw = _make_dream_worker(tmp_path, monkeypatch)
    completed: list[str] = []

    async def _fail():
        raise RuntimeError("simulated failure")

    def _ok(name):
        completed.append(name)

    with patch.object(dw, "consolidate_memory", new=AsyncMock(side_effect=_fail)), \
         patch.object(dw, "self_critique",      new=AsyncMock(side_effect=lambda: _ok("self_critique"))), \
         patch.object(dw, "map_document_relationships", new=AsyncMock(side_effect=lambda: _ok("map"))), \
         patch.object(dw, "anticipate_tomorrow",        new=AsyncMock(side_effect=lambda: _ok("anticipate"))), \
         patch.object(dw, "refine_style_guide",         new=AsyncMock(side_effect=lambda: _ok("style"))):
        await dw.run_full_cycle()   # must not raise

    assert "self_critique" in completed
    assert "anticipate" in completed


# ── consolidate_memory ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consolidate_memory_no_sessions(tmp_path, monkeypatch):
    dw = _make_dream_worker(tmp_path, monkeypatch)
    dw._session_manager.get_today_sessions.return_value = []
    # Should complete silently without error
    with patch.object(dw, "_dream_call", new=AsyncMock(return_value="ok")):
        await dw.consolidate_memory()


# ── self_critique ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_self_critique_no_corrections(tmp_path, monkeypatch):
    dw = _make_dream_worker(tmp_path, monkeypatch)
    session = MagicMock()
    session.messages = [
        {"role": "user", "content": "What is the charity number?"},
        {"role": "assistant", "content": "It is SCO012345."},
    ]
    dw._session_manager.get_today_sessions.return_value = [session]

    with patch.object(dw, "_dream_call", new=AsyncMock(return_value="No issues.")):
        await dw.self_critique()

    # No pending change should be proposed when there are no corrections
    dw._pending_changes.propose.assert_not_called()


# ── _dream_call helper ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dream_call_uses_dream_endpoint(tmp_path):
    """_dream_call should always hit the Pi 5 dream model regardless of state."""
    dw = _make_dream_worker(tmp_path)
    with patch("app.skills.dream_worker.dream_chat", new=AsyncMock(return_value="dream response")) as mock_dc:
        result = await dw._dream_call("hello")
    assert result == "dream response"
    mock_dc.assert_called_once()
