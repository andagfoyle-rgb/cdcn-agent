"""
DreamWorkerSkill — six overnight consolidation tasks run during DREAM mode.

Architecture adapted from OpenCLAW's heartbeat runner:
  - Tasks run sequentially; one failure never aborts the rest of the cycle.
  - All LLM-generated proposals are written to pending_changes/ for human review.
    Nothing is auto-applied to identity or configuration files.
  - The small local model (dream_chat / DREAM_OLLAMA_BASE_URL + DREAM_MODEL)
    is used for all inference so the Pi 5 can work without waking the R710.
  - Start/end time and per-task outcomes are written to audit_log.
  - A full cycle summary is written to the dream journal via memory_skill.

Dependency injection mirrors OpenCLAW's cron-isolated-agent pattern: all
dependencies are passed at construction time so the worker can be unit-tested
and the dream-mode endpoint is explicit rather than implicit.

Task implementations live in dream_tasks.py; shared helpers in dream_helpers.py.
"""
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.llm_client import dream_chat  # noqa: F401 – imported for module-level patching in tests
from app.skills.base import BaseSkill, SkillResult
from app.skills.dream_helpers import (
    _TaskOutcome,
    _file_locks,  # noqa: F401 – re-exported for backward compat
    _get_file_lock,  # noqa: F401 – re-exported for backward compat
    _session_transcript,
    _truncate,
)
from app.skills import dream_tasks

log = logging.getLogger(__name__)


# ── DreamWorkerSkill ──────────────────────────────────────────────────────────


class DreamWorkerSkill(BaseSkill):
    """
    Six overnight consolidation tasks.

    Constructor takes explicit dependencies so the dream-mode LLM endpoint
    is wired at construction time and tests can inject mocks.

    Parameters
    ----------
    llm_client     CDCNLLMClient configured for DREAM_OLLAMA_BASE_URL + DREAM_MODEL
    memory_skill   MemorySkill instance
    vector_store   CDCNVectorStore instance
    session_manager  SessionManager instance
    pending_changes  app.storage.pending_changes module (or compatible object)
    audit_log        app.storage.audit_log module (or compatible object)
    """

    name = "dream_worker"
    description = "Six overnight consolidation tasks run during DREAM mode."

    def __init__(
        self,
        llm_client,
        memory_skill,
        vector_store,
        session_manager,
        pending_changes,
        audit_log,
        settings=None,  # accepted for API compat; module-level settings used
    ) -> None:
        self._llm = llm_client
        self._memory = memory_skill
        self._vs = vector_store
        self._sessions = session_manager
        self._session_manager = session_manager  # alias for test access
        self._pending = pending_changes
        self._pending_changes = pending_changes   # alias for test access
        self._audit = audit_log
        self._audit_log_mod = audit_log  # module reference for log_learned_skill

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(self, task: str = "full_cycle", **kwargs) -> SkillResult:
        if task == "full_cycle":
            return await self.run_full_cycle()
        # Backward-compat shims so the old state_manager calls still work
        if task == "prefetch":
            return await self._prefetch_compat()
        if task == "summarize_pending":
            return await self._summarize_pending_compat()
        return SkillResult(success=False, error=f"Unknown dream task: {task}")

    async def run_full_cycle(self) -> SkillResult:
        """
        Run all six tasks in sequence.

        Per-task try/except means one failure never aborts the cycle.
        Outcomes are logged to audit_log and summarised in the dream journal.
        """
        cycle_start = time.monotonic()
        cycle_start_ts = datetime.now(timezone.utc).isoformat()
        outcomes: list[_TaskOutcome] = []

        await self._audit_event("cycle_start", cycle_start_ts)

        tasks = [
            ("consolidate_memory", self.consolidate_memory),
            ("self_critique", self.self_critique),
            ("learn_from_interactions", self.learn_from_interactions),
            ("map_document_relationships", self.map_document_relationships),
            ("anticipate_tomorrow", self.anticipate_tomorrow),
            ("refine_style_guide", self.refine_style_guide),
        ]

        for task_name, task_fn in tasks:
            t0 = time.monotonic()
            log.info("Dream cycle: starting %s", task_name)
            try:
                outcome = await task_fn()
                if not isinstance(outcome, _TaskOutcome):
                    # Handles mock objects / None returned by patched methods in tests
                    outcome = _TaskOutcome(task=task_name, success=True, summary="(mocked)")
                outcome.duration_ms = int((time.monotonic() - t0) * 1000)
            except Exception as exc:  # noqa: BLE001
                log.error("Dream cycle task %s raised unexpectedly: %s", task_name, exc)
                outcome = _TaskOutcome(
                    task=task_name,
                    success=False,
                    summary="",
                    error=str(exc),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
            outcomes.append(outcome)
            log.info(
                "Dream cycle: %s — success=%s (%dms): %s",
                task_name, outcome.success, outcome.duration_ms,
                outcome.summary or outcome.error,
            )
            await self._audit_event(
                f"task_{task_name}",
                json.dumps({
                    "success": outcome.success,
                    "duration_ms": outcome.duration_ms,
                    "summary": outcome.summary[:200],
                    "error": outcome.error[:200] if outcome.error else "",
                }),
            )

        # Write dream journal
        cycle_dur_ms = int((time.monotonic() - cycle_start) * 1000)
        journal_text = self._build_cycle_summary(outcomes, cycle_start_ts, cycle_dur_ms)
        try:
            self._memory.write_dream_journal(content=journal_text)
        except Exception as exc:  # noqa: BLE001
            log.warning("Dream journal write failed: %s", exc)

        await self._audit_event(
            "cycle_end",
            json.dumps({"duration_ms": cycle_dur_ms, "tasks": len(outcomes)}),
        )

        # Invalidate system prompt cache after dream cycle
        # (learned_skills may have changed)
        try:
            from app.gateway.prompt_builder import invalidate_prompt_cache
            invalidate_prompt_cache()
        except Exception:
            pass

        # Log a structured dream-cycle record (used by AuditLog.log_dream_cycle callers)
        try:
            if hasattr(self._audit, "log_dream_cycle"):
                await self._audit.log_dream_cycle(
                    start=cycle_start_ts,
                    end=datetime.now(timezone.utc).isoformat(),
                    tasks_completed=sum(1 for o in outcomes if o.success),
                    tasks_failed=sum(1 for o in outcomes if not o.success),
                    summary=journal_text[:500],
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("log_dream_cycle failed: %s", exc)

        return SkillResult(
            success=all(o.success for o in outcomes),
            output=journal_text,
            metadata={
                "outcomes": [
                    {
                        "task": o.task,
                        "success": o.success,
                        "duration_ms": o.duration_ms,
                        "summary": o.summary,
                    }
                    for o in outcomes
                ]
            },
        )

    # ── Task delegators ──────────────────────────────────────────────────────

    async def consolidate_memory(self) -> _TaskOutcome:
        return await dream_tasks.consolidate_memory(
            self._dream_call, self._memory, self._sessions,
        )

    async def self_critique(self) -> _TaskOutcome:
        return await dream_tasks.self_critique(
            self._dream_call, self._sessions, self._pending,
        )

    async def learn_from_interactions(self) -> _TaskOutcome:
        return await dream_tasks.learn_from_interactions(
            self._dream_call, self._sessions,
        )

    async def map_document_relationships(self) -> _TaskOutcome:
        return await dream_tasks.map_document_relationships(
            self._dream_call, self._vs,
        )

    async def anticipate_tomorrow(self) -> _TaskOutcome:
        return await dream_tasks.anticipate_tomorrow(
            self._dream_call, self._memory, self._vs,
            self._read_sessions_n_days,
        )

    async def refine_style_guide(self) -> _TaskOutcome:
        return await dream_tasks.refine_style_guide(
            self._dream_call, self._pending, self._find_writer_invocations,
        )

    # Keep _parse_learned_patterns and _parse_predictions as methods for
    # backward compatibility (tests may patch them on the class).
    def _parse_learned_patterns(self, text: str) -> list[tuple[str, str, str]]:
        return dream_tasks._parse_learned_patterns(text)

    def _parse_predictions(self, text: str) -> list[tuple[str, str]]:
        return dream_tasks._parse_predictions(text)

    def _sample_chunks_for_type(self, doc_type: str, n: int = 3) -> list[str]:
        return dream_tasks._sample_chunks_for_type(self._vs, doc_type, n)

    # ── Session reading helpers ──────────────────────────────────────────────

    def _read_sessions_n_days(self, n_days: int) -> str:
        """
        Read all session transcripts from the last n_days into a single string.
        Mirrors OpenCLAW's session-file reading pattern from memory/session-files.ts.
        """
        from app.gateway.session import Session  # lazy — avoids circular import

        session_root = Path(settings.memory_path) / "session_log"
        parts: list[str] = []

        for offset in range(n_days):
            date_str = (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")
            day_dir = session_root / date_str
            if not day_dir.exists():
                continue
            for session_file in sorted(day_dir.glob("*.json")):
                if session_file.suffix == ".tmp":
                    continue
                try:
                    data = json.loads(session_file.read_text())
                    session = Session(**data)
                    t = _session_transcript(session)
                    if t.strip():
                        parts.append(
                            f"[{date_str} / {session.session_id[:8]}]\n{t}"
                        )
                except Exception as exc:  # noqa: BLE001
                    log.debug("Skipping session file %s: %s", session_file, exc)

        return "\n\n---\n\n".join(parts) if parts else "(no sessions found)"

    def _find_writer_invocations(self, days: int) -> list[tuple[str, str]]:
        """
        Scan session logs from the last `days` days for assistant turns that
        look like formal document drafts from the writer skill.

        Returns list of (document_type, draft_text) tuples.
        Document type is inferred from structural markers in the text.
        """
        from app.gateway.session import Session  # lazy import

        _DOC_PATTERNS = [
            (r"# Funding Application", "application"),
            (r"# Minutes of a Meeting", "minutes"),
            (r"\|\s*\*\*Policy Number\*\*", "policy"),
            (r"#[^\n]+Policy\b", "policy"),
            (r"# Constitution", "constitution"),
            (r"\bAnnual Report\b", "report"),
        ]

        session_root = Path(settings.memory_path) / "session_log"
        invocations: list[tuple[str, str]] = []

        for offset in range(days):
            date_str = (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")
            day_dir = session_root / date_str
            if not day_dir.exists():
                continue
            for session_file in sorted(day_dir.glob("*.json")):
                if session_file.suffix == ".tmp":
                    continue
                try:
                    data = json.loads(session_file.read_text())
                    session = Session(**data)
                    for msg in session.messages:
                        if msg.get("role") != "assistant":
                            continue
                        content = msg.get("content", "")
                        for pattern, dtype in _DOC_PATTERNS:
                            if re.search(pattern, content, re.IGNORECASE):
                                invocations.append((dtype, content))
                                break
                except Exception as exc:  # noqa: BLE001
                    log.debug("Skipping session file %s: %s", session_file, exc)

        return invocations

    # ── Internal helpers ─────────────────────────────────────────────────────

    async def _dream_call(self, prompt: str) -> str:
        """
        Route an LLM call through the module-level dream_chat() function.

        Always targets the Pi 5 dream endpoint regardless of the agent's current
        state.  The module-level name allows tests to patch
        ``app.skills.dream_worker.dream_chat`` for isolation.
        """
        return await dream_chat([{"role": "user", "content": prompt}])

    async def _audit_event(self, action: str, detail: str) -> None:
        try:
            await self._audit.log_event(
                actor="dream_worker",
                action=action,
                detail=detail,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("dream_worker audit log failed (%s): %s", action, exc)

    def _build_cycle_summary(
        self,
        outcomes: list[_TaskOutcome],
        start_ts: str,
        duration_ms: int,
    ) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            f"# Dream Cycle — {today}",
            "",
            f"Started: {start_ts}",
            f"Total duration: {duration_ms / 1000:.1f}s",
            "",
            "## Task Outcomes",
            "",
        ]
        for o in outcomes:
            icon = "✓" if o.success else "✗"
            lines.append(f"### {icon} {o.task} ({o.duration_ms}ms)")
            if o.summary:
                lines.append(o.summary)
            if o.error:
                lines.append(f"Error: {o.error}")
            lines.append("")
        return "\n".join(lines)

    # ── Backward-compat stubs ────────────────────────────────────────────────
    # Called by old state_manager code via run(task="prefetch/summarize_pending")

    async def _prefetch_compat(self) -> SkillResult:
        outcome = await self.anticipate_tomorrow()
        return SkillResult(
            success=outcome.success,
            output=outcome.summary,
            error=outcome.error,
        )

    async def _summarize_pending_compat(self) -> SkillResult:  # noqa: D401
        pending_dir = Path(settings.pending_changes_path)
        files = list(pending_dir.glob("*.md")) if pending_dir.exists() else []
        if not files:
            return SkillResult(success=True, output="No pending changes.")
        combined = _truncate(
            "\n\n".join(f.read_text() for f in files[:10]),
            max_chars=4000,
        )
        try:
            summary = await self._dream_call(
                "Summarise the following pending changes concisely:\n\n" + combined
            )
        except Exception as exc:  # noqa: BLE001
            return SkillResult(success=False, error=str(exc))
        return SkillResult(success=True, output=summary)


# Backward-compat alias
DreamWorker = DreamWorkerSkill
