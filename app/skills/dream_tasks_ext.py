"""
dream_tasks_ext — overnight consolidation tasks 4-6, extracted from the
original dream_worker.py.

Contains:
  - map_document_relationships  (task 4)
  - anticipate_tomorrow         (task 5)
  - refine_style_guide          (task 6)

Companion to dream_tasks.py (tasks 1-3).  Both modules receive their
dependencies explicitly so they can be unit-tested in isolation.
"""
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.skills.dream_helpers import (
    _KNOWLEDGE_GRAPH_FILE,
    _TaskOutcome,
    _get_file_lock,
    _read_optional,
    _truncate,
)

log = logging.getLogger(__name__)


# ── Task 4: map_document_relationships ───────────────────────────────────────


def _sample_chunks_for_type(vector_store, doc_type: str, n: int = 3) -> list[str]:
    """Return up to n random chunk texts for the given document_type."""
    try:
        col = vector_store._get_collection()
        results = col.get(
            where={"document_type": doc_type},
            include=["documents"],
        )
        docs: list[str] = results.get("documents") or []
        if not docs:
            return []
        return [d[:600] for d in random.sample(docs, min(n, len(docs)))]
    except Exception as exc:  # noqa: BLE001
        log.warning("_sample_chunks_for_type(%s) failed: %s", doc_type, exc)
        return []


async def map_document_relationships(
    dream_call,
    vector_store,
) -> _TaskOutcome:
    """
    Sample chunks from pairs of document types and ask the LLM to identify
    connections: shared projects, referenced decisions, linked funding.
    Appends findings to skills_config/memory/knowledge_graph.md.
    Capped at 20 pair comparisons per cycle.
    """
    # get_all_document_metadata returns one dict per chunk (async).
    # Fall back to list_documents (sync) if the method is unavailable.
    if callable(getattr(vector_store, "get_all_document_metadata", None)):
        try:
            documents = await vector_store.get_all_document_metadata()
        except TypeError:
            documents = vector_store.get_all_document_metadata()
    else:
        documents = vector_store.list_documents()
    if not documents:
        return _TaskOutcome(
            task="map_document_relationships",
            success=True,
            summary="No documents in store — skipping.",
        )

    by_type: dict[str, list[dict]] = {}
    for doc in documents:
        dtype = doc.get("document_type", "other")
        by_type.setdefault(dtype, []).append(doc)

    eligible = [dt for dt, docs in by_type.items() if len(docs) >= 2]
    if len(eligible) < 2:
        return _TaskOutcome(
            task="map_document_relationships",
            success=True,
            summary=(
                f"Only {len(eligible)} eligible document type(s) — "
                f"need at least 2 to compare."
            ),
        )

    pairs = [
        (eligible[i], eligible[j])
        for i in range(len(eligible))
        for j in range(i + 1, len(eligible))
    ]
    if len(pairs) > 20:
        pairs = random.sample(pairs, 20)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    findings: list[str] = []

    for type_a, type_b in pairs:
        chunks_a = _sample_chunks_for_type(vector_store, type_a, n=3)
        chunks_b = _sample_chunks_for_type(vector_store, type_b, n=3)
        if not chunks_a or not chunks_b:
            continue

        context_a = "\n---\n".join(f"[{type_a}] {c}" for c in chunks_a)
        context_b = "\n---\n".join(f"[{type_b}] {c}" for c in chunks_b)

        prompt = (
            "You are analysing documents held by CDCN (Community Development Company "
            "Nesting), a Scottish charitable organisation in South Nesting, Shetland.\n\n"
            f"Review sample chunks from two document types and identify any connections: "
            f"shared projects, referenced decisions, linked funding, or common themes.\n\n"
            f"DOCUMENT TYPE A — {type_a.upper()}:\n{_truncate(context_a, 1500)}\n\n"
            f"DOCUMENT TYPE B — {type_b.upper()}:\n{_truncate(context_b, 1500)}\n\n"
            "List connections as bullet points (max 5). "
            "If no meaningful connections are apparent, respond only with "
            "'No connections identified.'"
        )

        try:
            response = await dream_call(prompt)
            if "no connections identified" not in response.lower():
                findings.append(
                    f"### {type_a.capitalize()} ↔ {type_b.capitalize()}\n\n"
                    f"{response.strip()}"
                )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "map_document_relationships LLM failed (%s / %s): %s",
                type_a, type_b, exc,
            )

    if findings:
        async with _get_file_lock(_KNOWLEDGE_GRAPH_FILE):
            _KNOWLEDGE_GRAPH_FILE.parent.mkdir(parents=True, exist_ok=True)
            existing = _read_optional(_KNOWLEDGE_GRAPH_FILE) or "# CDCN Knowledge Graph\n"
            new_section = (
                f"\n## Document Relationships — {today}\n\n"
                + "\n\n".join(findings)
                + "\n"
            )
            _KNOWLEDGE_GRAPH_FILE.write_text(existing.rstrip() + new_section)

    return _TaskOutcome(
        task="map_document_relationships",
        success=True,
        summary=(
            f"Compared {len(pairs)} pair(s) across "
            f"{len(eligible)} document type(s). "
            f"Found {len(findings)} relationship(s)."
        ),
    )


# ── Task 5: anticipate_tomorrow ──────────────────────────────────────────────


def _parse_predictions(text: str) -> list[tuple[str, str]]:
    """Parse PREDICTION N / Question: / Search: blocks from LLM output."""
    results: list[tuple[str, str]] = []
    blocks = re.split(r"PREDICTION\s+\d+", text, flags=re.IGNORECASE)
    for block in blocks[1:]:
        q = re.search(r"Question:\s*(.+?)(?:\n|$)", block, re.IGNORECASE)
        s = re.search(r"Search:\s*(.+?)(?:\n|$)", block, re.IGNORECASE)
        if q and s:
            results.append((q.group(1).strip(), s.group(1).strip()))
    return results[:5]


async def anticipate_tomorrow(
    dream_call,
    memory_skill,
    vector_store,
    read_sessions_n_days_fn,
) -> _TaskOutcome:
    """
    Predict tomorrow's 5 most likely questions, run vector searches for
    each, and write a pre-loaded prefetch cache with an expiry timestamp.
    The router checks prefetch_cache.md before ChromaDB on each query.
    """
    funding_text = _read_optional(Path("skills_config/funding_deadlines.yaml"))
    sessions_text = read_sessions_n_days_fn(7)
    graph_text = _read_optional(_KNOWLEDGE_GRAPH_FILE)
    memory_text = memory_skill.load_memory()

    context = (
        f"## Upcoming Funding Deadlines\n"
        f"{funding_text or '(none recorded)'}\n\n"
        f"## Recent Activity (last 7 days)\n"
        f"{_truncate(sessions_text, 3000)}\n\n"
        f"## Knowledge Graph\n"
        f"{_truncate(graph_text, 1500)}\n\n"
        f"## Long-term Memory\n"
        f"{_truncate(memory_text, 1500)}"
    )

    prompt = (
        "You are the overnight planning assistant for CDCN Agent, serving a Scottish "
        "charitable organisation (Community Development Company Nesting, South Nesting, "
        "Shetland).\n\n"
        "Based on CDCN's recent activity and upcoming deadlines, predict the 5 most "
        "likely questions or tasks for tomorrow. For each, give a concise document "
        "search query.\n\n"
        "Format EXACTLY as:\n\n"
        "PREDICTION 1\n"
        "Question: [the likely question or task in one sentence]\n"
        "Search: [a short specific search query for relevant documents]\n\n"
        "PREDICTION 2\n...\n\n"
        f"Context:\n{context}"
    )

    try:
        response = await dream_call(prompt)
    except Exception as exc:  # noqa: BLE001
        return _TaskOutcome(
            task="anticipate_tomorrow",
            success=False,
            summary="",
            error=f"LLM call failed: {exc}",
        )

    predictions = _parse_predictions(response)
    if not predictions:
        _fallback_queries = [
            ("Latest board meeting decisions", "board meeting minutes decisions"),
            ("Upcoming funding deadlines", "funding deadlines grant application"),
        ]
        try:
            for question, search_query in _fallback_queries:
                await vector_store.search(search_query, n_results=4)
        except Exception:
            pass
        return _TaskOutcome(
            task="anticipate_tomorrow",
            success=True,
            summary="LLM returned no parseable predictions — ran fallback prefetch searches.",
        )

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cache_parts = [
        f"# Prefetch Cache",
        f"",
        f"Generated: {now_str}",
        f"Expires: {tomorrow}",
        f"",
    ]

    chunks_loaded = 0
    for i, (question, search_query) in enumerate(predictions, start=1):
        cache_parts.append(f"## Prediction {i}: {question}")
        cache_parts.append(f"*Search: {search_query}*")
        cache_parts.append("")
        try:
            hits = await vector_store.search(search_query, n_results=4)
            if hits:
                for hit in hits:
                    fname = Path(hit.get("source_file", "unknown")).name
                    page = hit.get("page_number", 0)
                    excerpt = hit.get("text", "")[:400]
                    cache_parts.append(
                        f"**From {fname} (page {page or '?'}):**\n{excerpt}"
                    )
                    cache_parts.append("")
                    chunks_loaded += 1
            else:
                cache_parts.append("*(no relevant documents found)*")
                cache_parts.append("")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "anticipate_tomorrow search failed for '%s': %s", search_query, exc
            )
            cache_parts.append(f"*(search failed)*")
            cache_parts.append("")

    prefetch_path = Path(settings.memory_path) / "prefetch_cache.md"
    async with _get_file_lock(prefetch_path):
        prefetch_path.parent.mkdir(parents=True, exist_ok=True)
        prefetch_path.write_text("\n".join(cache_parts))

    return _TaskOutcome(
        task="anticipate_tomorrow",
        success=True,
        summary=(
            f"Generated {len(predictions)} prediction(s), "
            f"pre-loaded {chunks_loaded} chunk(s). "
            f"Cache expires {tomorrow}."
        ),
    )


# ── Task 6: refine_style_guide ───────────────────────────────────────────────


async def refine_style_guide(
    dream_call,
    pending_changes,
    find_writer_invocations_fn,
) -> _TaskOutcome:
    """
    Find writer skill invocations across the last 7 days.
    If the same document type was drafted more than once, compare
    the approaches and propose additions to style_guide.md.
    All proposals go to pending_changes/ -- nothing is auto-applied.
    Skipped if fewer than 3 writer invocations today.
    """
    today_invocations = find_writer_invocations_fn(days=1)

    if len(today_invocations) < 3:
        return _TaskOutcome(
            task="refine_style_guide",
            success=True,
            summary=(
                f"Only {len(today_invocations)} writer invocation(s) today "
                f"(threshold: 3) — skipping."
            ),
        )

    # Look across the whole week for repeat drafts of the same type
    weekly_invocations = find_writer_invocations_fn(days=7)
    by_type: dict[str, list[str]] = {}
    for dtype, draft_text in weekly_invocations:
        by_type.setdefault(dtype, []).append(draft_text)

    repeated = {dt: drafts for dt, drafts in by_type.items() if len(drafts) > 1}
    if not repeated:
        return _TaskOutcome(
            task="refine_style_guide",
            success=True,
            summary="No document type drafted more than once this week — nothing to compare.",
        )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pending_dir = Path(settings.pending_changes_path)
    pending_dir.mkdir(parents=True, exist_ok=True)
    proposals_written = 0

    for dtype, drafts in repeated.items():
        prompt = (
            "You are reviewing two drafts of the same document type produced by "
            "CDCN Agent for a Scottish charitable organisation.\n\n"
            f"Document type: {dtype}\n\n"
            f"DRAFT A:\n{_truncate(drafts[0], 1500)}\n\n"
            f"DRAFT B:\n{_truncate(drafts[-1], 1500)}\n\n"
            "Compare these drafts and identify:\n"
            "1. What worked better (specific, actionable observations)\n"
            "2. Structural or phrasing choices that produced clearer output\n"
            "3. Proposed additions to the writing style guide (maximum 3 bullets)\n\n"
            "Format your response as:\n\n"
            "OBSERVATIONS:\n[2-3 sentences]\n\n"
            "PROPOSED STYLE GUIDE ADDITIONS:\n"
            "- [addition 1]\n"
            "- [addition 2]\n"
            "- [addition 3 if needed]"
        )

        try:
            response = await dream_call(prompt)
        except Exception as exc:  # noqa: BLE001
            log.warning("refine_style_guide LLM call failed for %s: %s", dtype, exc)
            continue

        proposal_path = pending_dir / f"style_{today}.md"
        block = (
            f"# Proposed Style Guide Addition\n\n"
            f"Generated: {today}\nDocument type: {dtype}\nStatus: pending\n\n"
            f"## Comparative Analysis\n\n{response.strip()}\n\n"
            f"---\n"
            f"*Generated by DreamWorkerSkill refine_style_guide. "
            f"Requires human review.*\n"
        )
        try:
            # Append if file exists (multiple types in one cycle)
            if proposal_path.exists():
                proposal_path.write_text(proposal_path.read_text() + f"\n\n{block}")
            else:
                proposal_path.write_text(block)
            pending_changes.propose(
                title=f"style_guide.md addition — {dtype} — {today}",
                diff=response.strip(),
                author="dream_worker",
            )
            proposals_written += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("refine_style_guide write failed: %s", exc)

    return _TaskOutcome(
        task="refine_style_guide",
        success=True,
        summary=(
            f"Compared {len(repeated)} document type(s) drafted multiple times. "
            f"Wrote {proposals_written} proposal(s) to pending_changes/."
        ),
    )
