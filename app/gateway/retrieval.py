"""
Retrieval and context augmentation — RAG injection, prefetch cache, writer
intercept, identity linking, and memory consolidation.

Cost-reduction measures 2 & 3:

Measure 2 — Smart context loading:
  - Track which documents have been loaded in the current session.
  - On follow-up messages, reuse already-loaded context instead of re-fetching.
  - Enforce per-query token budgets based on query classification.

Measure 3 — Query classification before retrieval:
  - SIMPLE_CHAT (greetings, thanks, yes/no): skip retrieval entirely.
  - KNOWLEDGE (factual questions): targeted retrieval, max ~50K tokens.
  - DOCUMENT_GEN (draft/write): relevant docs, max ~100K tokens.
  - CROSS_ARCHIVE (broad search): full retrieval, max ~125K tokens.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import settings
from app.gateway.query_classifier import QueryClass, TOKEN_BUDGETS, classify_query

if TYPE_CHECKING:
    from app.gateway.session import Session
    from app.llm_client import CDCNLLMClient
    from app.skills.base import BaseSkill

log = logging.getLogger(__name__)


# ── Session-level document tracking ──────────────────────────────────────────
# Maps session_id -> set of document citations already loaded in this session.
# Also tracks the full RAG context string so follow-ups can reuse it.

_session_loaded_docs: dict[str, set[str]] = {}
_session_rag_context: dict[str, str] = {}
_session_query_count: dict[str, int] = {}

# Maximum sessions to track (prevent unbounded growth)
_MAX_TRACKED_SESSIONS = 50


def _cleanup_session_tracking():
    """Evict oldest sessions if tracking exceeds limit."""
    if len(_session_loaded_docs) > _MAX_TRACKED_SESSIONS:
        # Remove half the entries (oldest by insertion order)
        to_remove = list(_session_loaded_docs.keys())[:_MAX_TRACKED_SESSIONS // 2]
        for sid in to_remove:
            _session_loaded_docs.pop(sid, None)
            _session_rag_context.pop(sid, None)
            _session_query_count.pop(sid, None)


def get_session_query_class(session_id: str, message: str) -> str:
    """Classify query and return the class. Exported for use by router."""
    return classify_query(message)


# ── Prefetch cache ───────────────────────────────────────────────────────────

def check_prefetch(message: str) -> str:
    """
    Scan prefetch_cache.md for sections whose keywords overlap with the
    incoming message.  Returns the first matching section (up to 600 chars)
    or empty string if nothing is relevant.
    """
    cache_path = Path(settings.memory_path) / "prefetch_cache.md"
    if not cache_path.exists():
        return ""
    try:
        content = cache_path.read_text()
    except OSError:
        return ""

    query_words = {w.lower() for w in message.split() if len(w) > 4}
    if not query_words:
        return ""

    for section in content.split("## Q:"):
        section_lower = section.lower()
        if any(w in section_lower for w in query_words):
            return section.strip()[:600]

    return ""


# ── RAG context injection ────────────────────────────────────────────────────

# Keywords that trigger document retrieval
_DOC_KEYWORDS = [
    "minute", "minutes", "meeting", "attended", "present", "apolog",
    "agenda", "policy", "policies", "report", "trustee", "trustees",
    "funding", "grant", "application", "plan", "strategy", "constitution",
    "who was", "who attended", "who are", "who is", "decision", "decided",
    "agreed", "action point", "action item", "discussed", "discussion",
    "what was", "what were", "what did", "what does", "what is the",
    "according to", "what happened", "tell me about", "find information",
    "download", "fetch", "send me", "get me the", "give me the",
    "can i have", "the file", "the document", "the pdf",
    "the policy", "the plan", "the agenda", "archive",
    "solar", "polycrub", "scrapstore", "gym", "energy", "nesting",
    "cdcn", "community", "budget", "cost", "project", "approved",
    "voted", "resolved", "shetland", "aald skul", "aald skül",
    "highland", "oscr", "charity", "board",
    "dream", "journal", "overnight", "dream mode", "consolidation",
]

_QUESTION_WORDS = (
    "what", "who", "when", "where", "how", "why", "which",
    "tell me", "show me", "find", "can you", "could you",
    "is there", "are there", "do we", "does cdcn", "have we",
)


async def build_rag_context(
    message: str,
    user_content: str,
    session_id: str = "",
    query_class: str = "",
) -> tuple[str, bool]:
    """
    Augment user_content with RAG-retrieved documents if the message needs
    document context.

    Cost-reduction measures applied:
    1. Skip retrieval entirely for SIMPLE_CHAT queries.
    2. Reuse already-loaded context for follow-up queries in the same session.
    3. Enforce per-query token budgets based on query classification.

    Returns (augmented_content, rag_injected).
    """
    # ── Measure 3: Skip retrieval for simple chat ────────────────────────
    if query_class == QueryClass.SIMPLE_CHAT:
        log.info("RAG skipped: query classified as simple_chat")
        return user_content, False

    _msg_lower = message.lower().strip()
    _is_question = (
        "?" in message
        or any(_msg_lower.startswith(w) for w in _QUESTION_WORDS)
        or len(message.split()) >= 8
    )
    _needs_rag = _is_question or any(k in _msg_lower for k in _DOC_KEYWORDS)
    if not _needs_rag:
        log.info("RAG skipped: no document keywords detected")
        return user_content, False

    # ── Measure 2: Check session cache for already-loaded context ────────
    if session_id and session_id in _session_rag_context:
        existing_context = _session_rag_context[session_id]
        loaded_docs = _session_loaded_docs.get(session_id, set())

        # Check if the follow-up question relates to already-loaded docs
        query_words = {w.lower() for w in message.split() if len(w) > 3}
        context_lower = existing_context.lower()
        overlap = sum(1 for w in query_words if w in context_lower)

        if overlap >= 2 and loaded_docs:
            # Reuse existing context — don't re-fetch
            log.info(
                "RAG reuse: session %s already has %d docs loaded, "
                "%d query words overlap — reusing cached context",
                session_id[:8], len(loaded_docs), overlap,
            )
            _rag_prefix = (
                "The relevant CDCN documents have ALREADY been retrieved for this question. "
                "DO NOT call search_archive — answer directly from the documents below. "
                "Use specific quotes, names, dates, and figures. "
                "CITE EVERY FACTUAL CLAIM with [Document Name, Date, Section]. "
                "If you cannot find a citation, say so explicitly.\n\n"
            )
            augmented = (
                f"{message}\n\n"
                f"--- RETRIEVED DOCUMENTS (answer from these) ---\n\n"
                f"{_rag_prefix}"
                f"{existing_context}"
            )
            return augmented, True

    # ── Get token budget for this query class ────────────────────────────
    char_budget = TOKEN_BUDGETS.get(query_class, TOKEN_BUDGETS[QueryClass.KNOWLEDGE])

    try:
        from app.skills.search import search as rag_search

        # Detect doc_type hint from query
        _rag_doc_type = None
        _ml = message.lower()
        if any(k in _ml for k in ["business plan", "plan"]):
            _rag_doc_type = ["plan"]
        elif any(k in _ml for k in ["policy", "policies"]):
            _rag_doc_type = ["policy"]
        elif any(k in _ml for k in ["funding", "grant", "application"]):
            _rag_doc_type = ["funding"]
        elif any(k in _ml for k in ["agm", "annual general"]):
            _rag_doc_type = ["agm_minutes"]
        elif any(k in _ml for k in ["report", "trustees report", "annual report"]):
            _rag_doc_type = ["report"]

        # Adjust top_k based on query class
        top_k = 15
        if query_class == QueryClass.KNOWLEDGE:
            top_k = 10
        elif query_class == QueryClass.CROSS_ARCHIVE:
            top_k = 20

        # Run parent-child search with full parent context
        _search_result = await rag_search(
            query=message,
            doc_type=_rag_doc_type,
            top_k=top_k,
            return_parents=True,
        )
        # If typed search returned few results, also do unfiltered search
        if _rag_doc_type and len(_search_result.hits) < 5:
            _unfiltered = await rag_search(
                query=message,
                top_k=top_k,
                return_parents=True,
            )
            _seen_pids = {h.parent_id for h in _search_result.hits if h.parent_id}
            for h in _unfiltered.hits:
                if h.parent_id not in _seen_pids:
                    _search_result.hits.append(h)
                    _seen_pids.add(h.parent_id)

        # Load dream journals if query is about dream/overnight activity
        _journal_context = ""
        if any(k in message.lower() for k in ["dream", "overnight", "journal", "consolidation"]):
            _journal_dir = Path("/var/lib/cdcn-agent/memory/journal")
            if _journal_dir.exists():
                _journal_parts = []
                _jchars = 0
                for jf in sorted(_journal_dir.iterdir(), reverse=True)[:5]:
                    if jf.is_file() and _jchars < 30000:
                        try:
                            jtext = jf.read_text()
                            _journal_parts.append(f"=== JOURNAL: {jf.name} ===\n{jtext}")
                            _jchars += len(jtext)
                        except Exception:
                            pass
                _journal_context = "\n\n".join(_journal_parts)

        if _search_result.hits or _journal_context:
            _context_parts = []
            _total_chars = 0
            _loaded_citations = set()

            for hit in _search_result.hits:
                content_len = len(hit.content)
                # ── Measure 2: Enforce token budget ──────────────────
                if _total_chars + content_len > char_budget:
                    log.info(
                        "RAG budget reached: %d/%d chars, skipping remaining %d hits",
                        _total_chars, char_budget,
                        len(_search_result.hits) - len(_context_parts),
                    )
                    break
                _context_parts.append(f"=== {hit.citation} ===\n{hit.content}")
                _loaded_citations.add(hit.citation)
                _total_chars += content_len

            _docs_context = "\n\n".join(_context_parts)

            # Build file list for download links
            _docs_dir = Path(settings.watched_folder)
            _file_list = ", ".join(
                str(f.relative_to(_docs_dir))
                for f in sorted(_docs_dir.rglob("*"))
                if f.is_file()
            )

            _combined = "\n\n".join(filter(None, [_journal_context, _docs_context]))
            _rag_prefix = (
                "The relevant CDCN documents have ALREADY been retrieved for this question. "
                "DO NOT call search_archive — answer directly from the documents below. "
                "Use specific quotes, names, dates, and figures. "
                "CITE EVERY FACTUAL CLAIM with [Document Name, Date, Section]. "
                "If you cannot find a citation, say so explicitly.\n\n"
                "DOCUMENT DOWNLOADS: If relevant, provide download links using: "
                f"/api/archive/download?path=RELATIVE_PATH — available files: {_file_list}\n"
                "URL-encode spaces as %20.\n\n"
            )
            augmented = (
                f"{message}\n\n"
                f"--- RETRIEVED DOCUMENTS (answer from these) ---\n\n"
                f"{_rag_prefix}"
                f"{_combined}"
            )

            # ── Measure 2: Cache loaded docs for this session ────────
            if session_id:
                _cleanup_session_tracking()
                _session_loaded_docs.setdefault(session_id, set()).update(_loaded_citations)
                _session_rag_context[session_id] = _combined
                _session_query_count[session_id] = _session_query_count.get(session_id, 0) + 1

            log.info(
                "RAG injected: %d parent sections, %d children matched, "
                "chars=%d (budget=%d, class=%s)",
                len(_search_result.hits), _search_result.total_children_matched,
                len(_combined), char_budget, query_class,
            )
            return augmented, True
        else:
            log.info("RAG: no relevant documents found for query")
    except Exception as e:
        log.warning("RAG failed: %s", e)

    return user_content, False


# ── Writer skill intercept ───────────────────────────────────────────────────

_WRITE_KEYWORDS = [
    "draft", "write me", "create a document", "prepare a",
    "funding application", "board minute", "governance policy",
    "trustees report", "trustee report", "annual report",
    "write a funding", "draft a funding", "help me write",
    "can you draft", "can you write", "can you prepare",
    "create a funding", "prepare a funding",
]


async def handle_writer_intercept(
    message: str, role: str, skills: dict[str, BaseSkill], session: Session,
) -> bool:
    """
    If the message asks to draft a document and the user has permission,
    run the writer skill with RAG context and append results to session.

    Returns True if the writer skill was invoked, False otherwise.
    """
    _wants_document = any(k in message.lower() for k in _WRITE_KEYWORDS)
    if not _wants_document or role not in ("admin", "staff"):
        return False

    try:
        _msg_lower = message.lower()
        if any(k in _msg_lower for k in ["trustee", "trustees", "trustees report",
                   "annual report", "trustee report", "yearly report"]):
            _template = "trustees_report"
        elif any(k in _msg_lower for k in ["funding", "application", "grant"]):
            _template = "funding_application"
        elif any(k in _msg_lower for k in ["minute", "minutes"]):
            _template = "board_minute"
        elif any(k in _msg_lower for k in ["policy", "governance"]):
            _template = "governance_policy"
        else:
            _template = "funding_application"

        log.info("Direct writer intercept: template=%s", _template)
        writer_skill = skills.get("writer")
        if not writer_skill:
            return False

        # Run multiple RAG searches to gather context for the document
        _extra_context = ""
        try:
            from app.skills.search import SearchSkill
            _search = SearchSkill()
            context_parts = []

            _queries = [message]
            if _template == "trustees_report":
                _queries.extend([
                    "board meeting minutes decisions actions",
                    "funding grants applications received",
                    "staff volunteering governance AGM",
                    "projects achievements community development",
                    "scrapstore gym polycrub energy solar",
                    "housing childcare community action plan",
                    "trustee report annual review",
                ])
            elif _template == "funding_application":
                _queries.extend([
                    "funding grants budget costs",
                    "project outcomes community benefit",
                    "organisation capacity staff volunteers",
                ])
            elif _template == "board_minute":
                _queries.extend([
                    "board meeting decisions resolutions",
                    "action points agreed tasks",
                ])
            elif _template == "governance_policy":
                _queries.extend([
                    "governance policy procedures",
                    "OSCR charity compliance",
                ])

            seen_excerpts: set[str] = set()
            for q in _queries:
                try:
                    sr = await _search.run(query=q, n_results=10)
                    if sr.success and sr.output:
                        for line in sr.output.split("\n\n"):
                            key = line[:100]
                            if key not in seen_excerpts:
                                seen_excerpts.add(key)
                                context_parts.append(line)
                except Exception as e:
                    log.debug("Search query failed: %s — %s", q, e)

            if context_parts:
                _extra_context = "\n\n".join(context_parts)
                log.info("RAG gathered %d unique excerpts for writer skill", len(context_parts))
        except Exception as e:
            log.warning("RAG context gathering failed: %s", e)

        result = await writer_skill.run(
            template=_template, brief=message,
            context=_extra_context if _extra_context else "",
        )
        if result.success:
            _dl_info = ""
            if result.metadata.get("download_url"):
                _dl_info = (
                    f"\n\nA Word document has been generated and is ready to download: "
                    f"{result.metadata['download_url']}"
                )
            session.messages.append({
                "role": "user",
                "content": (
                    f"[The writer skill has drafted a document based on the user's request. "
                    f"The draft is {len(result.output)} characters long. "
                    f"Summarise what was produced in 2-3 sentences and tell the user "
                    f"they can download it using the link below. "
                    f"Do NOT reproduce the full document text.]{_dl_info}"
                ),
            })
            log.info("Writer skill completed, passing to LLM for summary")
            return True
    except Exception as e:
        log.warning("Direct writer intercept failed: %s", e)

    return False


# ── Identity linking ─────────────────────────────────────────────────────────

def find_matching_web_user(display_name: str) -> str | None:
    """
    Check if a Discord display name matches a known web user.
    Returns the web username if a match is found, None otherwise.
    """
    from app.auth.auth import list_users
    normalised = display_name.strip().lower()
    if not normalised:
        return None
    first_name = normalised.split()[0]

    for u in list_users():
        if not u.get("active"):
            continue
        uname = (u.get("username") or "").lower()
        udisp = (u.get("display_name") or "").lower()
        if normalised in (uname, udisp) or first_name == uname:
            return u["username"]
    return None


# ── Memory consolidation ────────────────────────────────────────────────────

async def consolidate_memory(session: Session, llm: CDCNLLMClient, memory_skill) -> None:
    """
    Ask the LLM to summarise the last 20 messages into key facts and
    action items, then store them via memory_skill.

    Runs as a background Task — failures are logged, never propagated.
    """
    log.info(
        "Memory consolidation triggered: session=%s exchange=%d",
        session.session_id,
        session.exchange_count,
    )
    try:
        recent = session.messages[-20:]
        transcript = "\n".join(
            f"{m['role'].upper()}: {str(m.get('content', ''))[:400]}"
            for m in recent
        )
        summary_messages = [
            {
                "role": "user",
                "content": (
                    "Summarise the following conversation into a concise bullet-point "
                    "list of key facts, decisions, and action items for future reference:\n\n"
                    f"{transcript}"
                ),
            }
        ]
        summary = await llm.chat(
            summary_messages,
            system_prompt=(
                "You are a helpful assistant summarising a conversation "
                "for long-term memory storage. Be concise and factual."
            ),
        )
        await memory_skill.run(
            action="store_summary",
            summary=summary,
            session_id=session.session_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Memory consolidation failed for session %s: %s", session.session_id, exc)
