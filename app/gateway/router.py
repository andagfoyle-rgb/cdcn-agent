"""
AgentRouter — agentic loop orchestrator.
gateway_router — FastAPI APIRouter exposed to main.py (/api prefix).

The agentic loop delegates to:
  - prompt_builder.py  — system prompt cache, tool definitions
  - tool_handler.py    — injection detection, skill parsing/execution, response cleaning
  - retrieval.py       — RAG context injection, prefetch, writer intercept, memory
  - query_classifier.py — lightweight query classification for retrieval gating
  - token_tracker.py   — per-call token usage logging and cost estimation

Agentic loop (per OpenCLAW pattern, adapted for Python / Ollama):
  1. Prompt-injection guard
  2. Load or create session
  3. Classify query (simple_chat / knowledge / document_gen / cross_archive)
  4. Get system prompt (hash-based cache — identical across calls for prefix caching)
  5. Prefetch cache lookup for quick context
  6. Append user message to history (with RAG context gated by query class)
  7. First LLM call (non-streaming — need full text to detect skill call)
  8. If response contains a fenced JSON skill call: execute skill, re-prompt
  9. Stream final response to caller
 10. Log token usage and estimated cost
 11. Persist session; at every 10th exchange trigger async memory consolidation
"""
import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth.auth import User, check_rate_limit, get_current_user
from app.config import settings
from app.gateway import identity_map
from app.gateway.prompt_builder import (
    NO_MORE_TOOLS_SUFFIX,
    _PROMPT_CACHE,
    build_tool_definitions,
    get_system_prompt,
    get_prompt_cache_info,
)
from app.gateway.query_classifier import QueryClass, classify_query
from app.gateway.retrieval import (
    build_rag_context,
    check_prefetch,
    consolidate_memory,
    find_matching_web_user,
    handle_writer_intercept,
)
from app.gateway.session import Session, SessionManager
from app.gateway.token_tracker import (
    check_daily_cost_alert,
    get_today_usage,
    log_token_usage,
)
from app.gateway.tool_handler import (
    MAX_TOOL_ROUNDS,
    clean_llm_response,
    execute_skill,
    is_injection,
    parse_skill_call,
    parse_xml_tool_call,
    strip_xml_tool_calls,
    verify_response,
)
from app.llm_client import CDCNLLMClient, CDCNLLMError, llm_client
from app.skills.base import BaseSkill
from app.state_manager import state
from app.storage.audit_log import log_event

log = logging.getLogger(__name__)


# ── AgentRouter ──────────────────────────────────────────────────────────────


class AgentRouter:
    """
    Orchestrates the full agentic turn: guard → session → classify → prompt →
    LLM → skill → final response → token tracking → persist → consolidation.

    Intended to be instantiated once (module-level singleton via _get_router()).
    """

    def __init__(
        self,
        skills: dict[str, BaseSkill],
        llm: CDCNLLMClient,
        memory_skill,
    ) -> None:
        self.skills = skills
        self.llm = llm
        self.memory_skill = memory_skill
        self._sessions = SessionManager()
        self._pending_links: dict[str, str] = {}
        self._declined_links: set[str] = set()

    # ── Public entry point ───────────────────────────────────────────────────

    async def handle_message(
        self,
        user_id: str,
        role: str,
        message: str,
        channel_id: str = "",
        display_name: str = "",
    ) -> AsyncGenerator[str, None]:
        """
        Full agentic turn.  Yields text tokens suitable for StreamingResponse.

        Never raises — errors are yielded as human-readable messages so the
        caller always receives a complete response.
        """
        log.info("handle_message called: user=%s message=%s", user_id, message[:50])

        # ── Identity linking: handle "yes" / "no" responses to link prompts ──
        if user_id.startswith("discord:") and not identity_map.is_linked(user_id):
            _answer = message.strip().lower()
            if _answer in ("yes", "yep", "yeah", "aye", "y"):
                pending = self._pending_links.pop(user_id, None)
                if pending:
                    identity_map.link(user_id, pending)
                    yield (
                        f"Thanks! I've linked your Discord account to your "
                        f"web account ({pending.split(':', 1)[1]}). "
                        f"Your conversation history is now shared across both."
                    )
                    return
            elif _answer in ("no", "nope", "nah", "n"):
                if self._pending_links.pop(user_id, None):
                    self._declined_links.add(user_id)
                    yield "No problem — I'll keep your accounts separate."
                    return

        # ── 0. Rate limit ────────────────────────────────────────────────────
        if not check_rate_limit(user_id):
            yield "Request rate limit reached (20 per minute). Please wait a moment."
            return

        # ── 1. Prompt injection guard ────────────────────────────────────────
        if is_injection(message):
            await log_event(
                actor=user_id,
                action="injection_rejected",
                target=channel_id,
                detail=message[:300],
            )
            yield (
                "I'm sorry, I can't process that request. "
                "Please rephrase and try again."
            )
            return

        # ── 1a. Identity linking check for Discord users ─────────────────────
        if (
            user_id.startswith("discord:")
            and display_name
            and not identity_map.is_linked(user_id)
            and user_id not in self._pending_links
            and user_id not in self._declined_links
        ):
            match = find_matching_web_user(display_name)
            if match:
                self._pending_links[user_id] = f"web:{match}"
                yield (
                    f"Hi {display_name}! I notice your name matches "
                    f"a web account ({match}). Are you the same person? "
                    f"If so, I can link your accounts so we share the same "
                    f"conversation history across Discord and the website. "
                    f"Just reply **yes** or **no**."
                )
                return

        # ── 2. Session ───────────────────────────────────────────────────────
        session = self._sessions.load_or_create(user_id, role)

        # ── 3. Classify query (Measure 3) ────────────────────────────────────
        query_class = classify_query(message)
        log.info("Query classified: class=%s message=%s", query_class, message[:60])

        # ── 4. System prompt (hash-based cache — Measure 1) ──────────────────
        system_prompt = await get_system_prompt(role, self.memory_skill, self.skills)

        log.info("System prompt loaded, making LLM call")
        # ── 5. Prefetch cache ────────────────────────────────────────────────
        prefetch_ctx = ""
        if query_class != QueryClass.SIMPLE_CHAT:
            prefetch_ctx = check_prefetch(message)

        # ── 6. Append user message (RAG context merged in below) ─────────────
        user_content = message
        if prefetch_ctx:
            user_content = (
                f"{message}\n\n"
                f"[Relevant pre-loaded context]\n{prefetch_ctx}"
            )

        # ── 6a. Parent-child RAG injection (gated by query class) ────────────
        user_content, _rag_injected = await build_rag_context(
            message, user_content,
            session_id=session.session_id,
            query_class=query_class,
        )

        self._current_role = role
        session.messages.append({"role": "user", "content": user_content,
                                   "_ts": datetime.now(timezone.utc).isoformat()})

        # ── 6b. Direct writer skill intercept ────────────────────────────────
        await handle_writer_intercept(message, role, self.skills, session)

        # ── 7–9. LLM → optional skill → final response ──────────────────────
        response_text = ""
        skill_used = ""

        try:
            # First call: non-streaming with tool definitions
            tools = build_tool_definitions(self.skills)
            if _rag_injected:
                tools = [t for t in tools if t["function"]["name"] != "search_archive"]
            first_result = await self.llm.chat_with_tools(
                session.messages,
                system_prompt=system_prompt,
                tools=tools,
                user_id=user_id, role=role,
            )

            first_content = first_result.get("content", "")
            tool_calls = first_result.get("tool_calls", [])

            # ── 8. Tool call detection ───────────────────────────────────────
            log.info("LLM first response: content=%s tool_calls=%s",
                     (first_content or "")[:200],
                     [tc["name"] for tc in tool_calls])

            if tool_calls:
                response_text, skill_used = await self._handle_tool_calls(
                    tool_calls, first_content, session, system_prompt, user_id, role,
                )
                if response_text:
                    yield response_text

            else:
                # No tool call via the API — check for GLM-5 XML format
                xml_call = parse_xml_tool_call(first_content)
                if xml_call:
                    response_text, skill_used = await self._handle_xml_tool_call(
                        xml_call, first_content, session, system_prompt, user_id, role,
                    )
                    if response_text:
                        yield response_text
                    if response_text:
                        session.messages.append(
                            {"role": "assistant", "content": response_text,
                             "_ts": datetime.now(timezone.utc).isoformat()}
                        )
                        session.exchange_count += 1
                        self._sessions.save(session)
                    return

                # No tool call — check Ollama regex fallback
                if not self.llm._is_openai_compat:
                    skill_call = parse_skill_call(first_content)
                    if skill_call:
                        response_text = await self._handle_ollama_skill_call(
                            skill_call, first_content, session, system_prompt, user_id, role,
                        )
                        if response_text:
                            yield response_text
                        session.messages.append({"role": "assistant", "content": response_text,
                                                 "_ts": datetime.now(timezone.utc).isoformat()})
                        session.exchange_count += 1
                        self._sessions.save(session)
                        return

                response_text = clean_llm_response(first_content)
                if response_text:
                    yield response_text

        except CDCNLLMError as exc:
            log.error("LLM error in handle_message for user=%s: %s", user_id, exc)
            yield (
                "I encountered an error reaching the language model. "
                "Please try again in a moment."
            )
            response_text = f"[LLM ERROR: {exc}]"

        # ── 10. Persist session ──────────────────────────────────────────────
        if response_text:
            session.messages.append(
                {"role": "assistant", "content": response_text,
                 "_ts": datetime.now(timezone.utc).isoformat()}
            )
        session.exchange_count += 1
        self._sessions.save(session)

        # ── 11. Check daily cost alert (async, non-blocking) ─────────────────
        asyncio.create_task(self._check_cost_alert(), name="cost-alert-check")

        # ── 12. Memory consolidation every 10 exchanges ──────────────────────
        if session.exchange_count > 0 and session.exchange_count % 10 == 0:
            asyncio.create_task(
                consolidate_memory(session, self.llm, self.memory_skill),
                name=f"consolidate-{session.session_id}-{session.exchange_count}",
            )

    # ── Cost alert check ──────────────────────────────────────────────────────

    async def _check_cost_alert(self):
        """Check daily cost and post alert if over threshold."""
        try:
            alert = await check_daily_cost_alert()
            if alert:
                log.warning("COST ALERT: %s", alert)
                # Post to noticeboard via shared_messages
                try:
                    from app.storage.audit_log import _adb, _ts
                    async with _adb() as conn:
                        await conn.execute(
                            "INSERT INTO shared_messages "
                            "(ts, username, display_name, role, msg_type, content) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (_ts(), "system", "CDCN Agent", "system", "alert", alert),
                        )
                        await conn.commit()
                except Exception:
                    pass  # Best-effort
        except Exception:
            pass  # Never let cost checks break the main flow

    # ── Tool call handlers (extracted from handle_message for readability) ───

    async def _handle_tool_calls(
        self, tool_calls, first_content, session, system_prompt, user_id, role,
    ) -> tuple[str, str]:
        """Handle OpenAI-format tool calls. Returns (response_text, skill_used)."""
        tc = tool_calls[0]
        skill_name = tc["name"]
        skill_args = tc["arguments"]
        skill_used = skill_name

        session.messages.append({
            "role": "assistant",
            "content": first_content or "",
            "tool_calls": [{"id": tc["id"], "type": "function",
                            "function": {"name": skill_name,
                                         "arguments": json.dumps(skill_args)}}],
        })

        skill_result = await execute_skill(skill_name, skill_args, self.skills)

        _dl_url = ""
        if isinstance(skill_result, dict) and skill_result.get("metadata", {}).get("download_url"):
            _dl_url = (
                f"\n\nIMPORTANT: A DOCX document was generated. "
                f"Include this download link in your response: "
                f"{skill_result['metadata']['download_url']}"
            )
        session.messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": json.dumps(skill_result, default=str) + _dl_url,
        })

        return await self._stream_final_response(
            session, system_prompt, skill_used, skill_result, user_id, role,
        )

    async def _handle_xml_tool_call(
        self, xml_call, first_content, session, system_prompt, user_id, role,
    ) -> tuple[str, str]:
        """Handle GLM-5 XML-format tool calls. Returns (response_text, skill_used)."""
        skill_name = xml_call["name"]
        skill_args = xml_call["args"]
        skill_used = skill_name
        log.info("XML tool-call fallback: skill=%s args=%s", skill_name, skill_args)

        preamble = strip_xml_tool_calls(first_content)

        skill_result = await execute_skill(skill_name, skill_args, self.skills)

        if preamble:
            session.messages.append({"role": "assistant", "content": preamble})
        _dl_url = ""
        if isinstance(skill_result, dict) and skill_result.get("metadata", {}).get("download_url"):
            _dl_url = (
                f"\n\nIMPORTANT: A DOCX document was generated. "
                f"Include this download link in your response: "
                f"{skill_result['metadata']['download_url']}"
            )
        session.messages.append({
            "role": "user",
            "content": (
                f"[Result of skill '{skill_name}']\n"
                f"{json.dumps(skill_result, indent=2, default=str)}{_dl_url}"
            ),
        })

        return await self._stream_final_response(
            session, system_prompt, skill_used, skill_result, user_id, role,
        )

    async def _handle_ollama_skill_call(
        self, skill_call, first_content, session, system_prompt, user_id, role,
    ) -> str:
        """Handle Ollama regex-based skill calls. Returns response_text."""
        skill_name = skill_call.get("skill", "")
        skill_args = skill_call.get("args", {})
        skill_used = skill_name
        skill_result = await execute_skill(skill_name, skill_args, self.skills)

        session.messages.append({"role": "assistant", "content": first_content})
        _dl_url = ""
        if isinstance(skill_result, dict) and skill_result.get("metadata", {}).get("download_url"):
            _dl_url = f"\n\nIMPORTANT: Include this download link: {skill_result['metadata']['download_url']}"
        session.messages.append({"role": "user", "content":
            f"[Result of skill '{skill_name}']\n{json.dumps(skill_result, indent=2, default=str)}{_dl_url}"})

        response_text, _ = await self._stream_final_response(
            session, system_prompt, skill_used, skill_result, user_id, role,
        )
        return response_text

    async def _stream_final_response(
        self, session, system_prompt, skill_used, skill_result, user_id, role,
    ) -> tuple[str, str]:
        """
        Buffer and clean the streaming final response after a tool call.
        Retries up to MAX_TOOL_ROUNDS if the LLM re-emits tool calls.
        Returns (response_text, skill_used).
        """
        _final_prompt = system_prompt + NO_MORE_TOOLS_SUFFIX
        for _tool_round in range(MAX_TOOL_ROUNDS):
            log.info("Final response attempt %d after tool call '%s'",
                     _tool_round + 1, skill_used)
            _buf: list[str] = []
            async for token in self.llm.chat_stream(
                session.messages,
                system_prompt=_final_prompt,
                skill_used=skill_used,
                user_id=user_id, role=role,
            ):
                _buf.append(token)
            _raw_text = "".join(_buf)
            response_text = clean_llm_response(_raw_text)
            log.info("Buffered %d raw chars → %d clean chars (round %d)",
                     len(_raw_text), len(response_text), _tool_round + 1)

            if response_text:
                if skill_used in ("search", "search_archive"):
                    response_text = await verify_response(
                        response_text, session.messages, self.llm,
                    )
                return response_text, skill_used

            # Check if the raw text is another tool call we can execute
            _re_call = parse_xml_tool_call(_raw_text)
            if _re_call and _tool_round < MAX_TOOL_ROUNDS - 1:
                _re_name = _re_call["name"]
                _re_args = _re_call["args"]
                log.info("LLM re-emitted tool call '%s' in final response — "
                         "executing (round %d)", _re_name, _tool_round + 1)
                _re_result = await execute_skill(_re_name, _re_args, self.skills)
                session.messages.append({
                    "role": "user",
                    "content": (
                        f"[Result of skill '{_re_name}']\n"
                        f"{json.dumps(_re_result, indent=2, default=str)}\n\n"
                        f"Now please answer the user's original question using "
                        f"all the tool results above. Do NOT call any more tools."
                    ),
                })
                skill_used = _re_name
                skill_result = _re_result
                continue

            if _raw_text.strip():
                log.warning("_clean_llm_response stripped entire response! "
                            "raw=%r", _raw_text[:500])
        else:
            # Exhausted all tool rounds
            log.warning("LLM failed to produce a clean response after %d "
                        "tool rounds — yielding skill result summary",
                        MAX_TOOL_ROUNDS)
            return (
                "I found the information but had trouble formatting the "
                "response. Here is what the skill returned:\n\n"
                + json.dumps(skill_result, indent=2, default=str)[:3000]
            ), skill_used


# ── Singleton factory ────────────────────────────────────────────────────────

_router_instance: AgentRouter | None = None


def set_router_instance(router: AgentRouter) -> None:
    """
    Register the fully-constructed AgentRouter singleton.
    Called once from main.py after all dependencies are wired.
    """
    global _router_instance
    _router_instance = router


def _get_agent_router() -> AgentRouter:
    """
    Return the pre-registered singleton.  Falls back to building a minimal
    router (without DreamWorkerSkill) if called before main.py has run —
    this only happens during unit tests or early startup edge cases.
    """
    global _router_instance
    if _router_instance is not None:
        return _router_instance

    from app.skills.indexer import IndexerSkill
    from app.skills.memory import MemorySkill
    from app.skills.search import SearchSkill
    from app.skills.writer import WriterSkill
    from app.skills.skill_builder import SkillBuilderSkill
    from app.skills.deadline_tracker import DeadlineTrackerSkill
    from app.skills.action_tracker import ActionTrackerSkill
    from app.skills.meeting_prep import MeetingPrepSkill

    memory_skill = MemorySkill()
    skills: dict[str, BaseSkill] = {
        "search": SearchSkill(),
        "indexer": IndexerSkill(),
        "writer": WriterSkill(),
        "skill_builder": SkillBuilderSkill(),
        "memory": memory_skill,
        "deadline_tracker": DeadlineTrackerSkill(),
        "action_tracker": ActionTrackerSkill(),
        "meeting_prep": MeetingPrepSkill(),
    }
    _router_instance = AgentRouter(
        skills=skills,
        llm=llm_client,
        memory_skill=memory_skill,
    )
    return _router_instance


# ── FastAPI router ───────────────────────────────────────────────────────────
# gateway_router is imported by main.py and mounted at /api.

gateway_router = APIRouter()


# ── /chat ────────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    user_id: str = ""
    role: str = "user"
    channel_id: str = ""


@gateway_router.post("/chat", tags=["agent"])
async def chat_endpoint(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Stream a response from the agent.

    Internally runs the full agentic loop:
    injection guard → session → classify → skill detection → LLM → persist.
    """
    agent = _get_agent_router()
    uid = body.user_id or current_user.username

    async def _stream():
        async for token in agent.handle_message(
            user_id=uid,
            role=body.role,
            message=body.message,
            channel_id=body.channel_id,
        ):
            yield token

    return StreamingResponse(_stream(), media_type="text/plain; charset=utf-8")


# ── /status ──────────────────────────────────────────────────────────────────


@gateway_router.get("/status", tags=["gateway"])
async def gateway_status():
    return state.status()


# ── /usage — Token usage and cost tracking (Measure 4) ──────────────────────


@gateway_router.get("/usage", tags=["gateway"])
async def usage_endpoint(current_user: User = Depends(get_current_user)):
    """
    Return today's token usage breakdown and estimated cost.

    Includes totals, breakdown by query class, and breakdown by user.
    """
    usage = await get_today_usage()
    prompt_info = get_prompt_cache_info()
    usage["prompt_cache"] = prompt_info
    return usage


# ── Session HTTP endpoints ───────────────────────────────────────────────────


@gateway_router.get("/sessions", tags=["sessions"])
async def list_sessions(current_user: User = Depends(get_current_user)):
    """List today's sessions."""
    manager = SessionManager()
    sessions = manager.get_today_sessions()
    return [
        {
            "session_id": s.session_id,
            "user_id": s.user_id,
            "role": s.role,
            "exchange_count": s.exchange_count,
            "created_at": s.created_at,
            "last_active": s.last_active,
        }
        for s in sessions
    ]


@gateway_router.get("/sessions/{session_id}", tags=["sessions"])
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    """Return one session by ID (today's sessions only)."""
    manager = SessionManager()
    for s in manager.get_today_sessions():
        if s.session_id == session_id:
            return s
