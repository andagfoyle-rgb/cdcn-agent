"""
Query classifier — lightweight keyword-based classification to gate retrieval.

Classes:
  SIMPLE_CHAT       — greetings, thanks, yes/no, clarifications. No retrieval.
  KNOWLEDGE         — factual questions about CDCN. Targeted retrieval (~20-50K tokens).
  DOCUMENT_GEN      — draft/write/create requests. Relevant doc retrieval (~50-100K tokens).
  CROSS_ARCHIVE     — broad searches across all minutes/docs. Full retrieval (~100K+ tokens).

Uses keyword detection only — no LLM call.
"""
import re
import logging

log = logging.getLogger(__name__)


class QueryClass:
    SIMPLE_CHAT = "simple_chat"
    KNOWLEDGE = "knowledge"
    DOCUMENT_GEN = "document_gen"
    CROSS_ARCHIVE = "cross_archive"


# Token budgets per class (max document context characters; ~4 chars/token)
TOKEN_BUDGETS = {
    QueryClass.SIMPLE_CHAT: 0,             # No retrieval
    QueryClass.KNOWLEDGE: 200_000,         # ~50K tokens
    QueryClass.DOCUMENT_GEN: 400_000,      # ~100K tokens
    QueryClass.CROSS_ARCHIVE: 500_000,     # ~125K tokens
}

# ── Pattern sets ──────────────────────────────────────────────────────────────

# Simple chat: greetings, acknowledgements, single-word responses
_GREETING_STARTS = (
    "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
    "morning", "afternoon", "evening", "hiya", "howdy",
)
_THANKS_STARTS = (
    "thanks", "thank you", "cheers", "ta", "much appreciated",
    "brilliant", "great thanks", "perfect thanks", "lovely",
)
_SIMPLE_RESPONSES = {
    "yes", "no", "yep", "nope", "yeah", "nah", "aye", "ok", "okay",
    "sure", "right", "correct", "exactly", "agreed", "fine", "cool",
    "please", "go ahead", "carry on", "continue", "understood",
}

# Document generation keywords
_DOC_GEN_PATTERNS = [
    "draft", "write me", "write a", "create a document", "prepare a",
    "generate a", "produce a", "compose a", "put together a",
    "can you draft", "can you write", "can you prepare", "can you create",
    "help me write", "help me draft",
    "funding application", "board minute", "governance policy",
    "trustees report", "annual report", "board pack", "meeting prep",
]

# Cross-archive / broad search keywords
_CROSS_ARCHIVE_PATTERNS = [
    "all minutes", "all meetings", "all board", "all documents",
    "every meeting", "every minute", "every board",
    "across all", "summarise all", "summarize all", "summary of all",
    "last year", "this year", "over the years", "since 20",
    "history of", "complete history", "full history",
    "compare all", "trend", "pattern across",
]


def classify_query(message: str) -> str:
    """
    Classify a user message into one of four categories.
    Returns a QueryClass constant.
    """
    text = message.strip()
    lower = text.lower()
    words = lower.split()
    word_count = len(words)

    # ── Simple chat detection ────────────────────────────────────────────
    # Single word or very short responses
    if word_count <= 2 and lower.rstrip("!?.") in _SIMPLE_RESPONSES:
        return QueryClass.SIMPLE_CHAT

    # Greetings
    if any(lower.startswith(g) for g in _GREETING_STARTS) and word_count <= 8:
        # "Hello" is simple, but "Hello, can you find the minutes..." is not
        if word_count <= 4 or "?" not in text:
            return QueryClass.SIMPLE_CHAT

    # Thanks / acknowledgements
    if any(lower.startswith(t) for t in _THANKS_STARTS) and word_count <= 8:
        return QueryClass.SIMPLE_CHAT

    # Short affirmatives with no question marks
    if word_count <= 3 and "?" not in text:
        if lower.rstrip("!.") in _SIMPLE_RESPONSES:
            return QueryClass.SIMPLE_CHAT

    # ── Cross-archive detection ──────────────────────────────────────────
    if any(p in lower for p in _CROSS_ARCHIVE_PATTERNS):
        return QueryClass.CROSS_ARCHIVE

    # ── Document generation detection ────────────────────────────────────
    if any(p in lower for p in _DOC_GEN_PATTERNS):
        return QueryClass.DOCUMENT_GEN

    # ── Default: knowledge question ──────────────────────────────────────
    return QueryClass.KNOWLEDGE
