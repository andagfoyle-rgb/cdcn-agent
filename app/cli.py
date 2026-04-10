"""
CDCN Agent — command-line interface.

Usage (installed):  cdcn-agent COMMAND [OPTIONS]
Usage (dev):        python -m app.cli COMMAND [OPTIONS]

Commands that operate on files work without the service running.
Commands that change agent mode (wake/dream) call the HTTP API and
require the service to be running.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click

# ── Async runner ───────────────────────────────────────────────────────────────


def _run(coro) -> None:
    """Run an async coroutine from a synchronous Click command."""
    asyncio.run(coro)


# ── HTTP API helper ────────────────────────────────────────────────────────────


def _api_token() -> str:
    """Generate a short-lived admin JWT for CLI → service calls."""
    from app.auth.auth import User, create_access_token
    return create_access_token(User(username="cli", hashed_password="", role="admin"))


def _api_call(method: str, path: str, body: Optional[dict] = None) -> dict:
    """
    Make an authenticated HTTP call to the running service.
    Exits with an error message if the service is not reachable.
    """
    import httpx
    from app.config import settings

    url = f"http://{settings.gateway_bind_host}:{settings.gateway_port}{path}"
    token = _api_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=30) as client:
            if method.upper() == "GET":
                resp = client.get(url, headers=headers)
            else:
                resp = client.post(url, json=body or {}, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        click.echo(
            f"Cannot reach the CDCN Agent service at {url}.\n"
            "Is it running?  sudo systemctl status cdcn-agent",
            err=True,
        )
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        click.echo(f"Service returned {exc.response.status_code}: {exc.response.text}", err=True)
        sys.exit(1)


# ── CLI group ──────────────────────────────────────────────────────────────────


@click.group()
def cli():
    """CDCN Agent — community document AI assistant."""
    pass


# ── dream ──────────────────────────────────────────────────────────────────────

DREAM_TASKS = [
    "consolidate_memory",
    "self_critique",
    "map_document_relationships",
    "anticipate_tomorrow",
    "refine_style_guide",
]


@cli.command()
@click.option("--dry-run", is_flag=True, help="Run dream cycle locally with a mock LLM (no service needed).")
@click.option(
    "--task",
    type=click.Choice(DREAM_TASKS),
    default=None,
    help="Run a single dream task instead of the full cycle.",
)
def dream(dry_run: bool, task: Optional[str]):
    """
    Trigger a dream cycle.

    Without --dry-run, sends a mode-change request to the running service.
    With --dry-run, runs the dream worker directly using a mock LLM — useful
    for testing the dream pipeline without an active Ollama connection.
    """
    if dry_run or task:
        click.echo("Running dream cycle directly (dry-run mode)..." if dry_run else f"Running task: {task}")
        _run(_dream_direct(task=task, dry_run=dry_run))
    else:
        result = _api_call("POST", "/api/mode", {"mode": "dream"})
        click.echo(f"Mode change requested: {result}")


async def _dream_direct(task: Optional[str], dry_run: bool) -> None:
    """Run the dream worker directly for testing, with an optional mock LLM."""
    from app.gateway.session import SessionManager
    from app.skills.dream_worker import DreamWorkerSkill
    from app.skills.memory import MemorySkill
    from app.storage import audit_log, pending_changes
    from app.storage.vector_store import vector_store

    if dry_run:
        class _MockLLM:
            async def chat(self, messages, **kwargs) -> str:
                return "[dry-run] Mock LLM response — no real inference performed."
        lm = _MockLLM()
    else:
        from app.llm_client import llm_client
        lm = llm_client

    memory = MemorySkill()
    session_manager = SessionManager()
    dw = DreamWorkerSkill(
        llm_client=lm,
        memory_skill=memory,
        vector_store=vector_store,
        session_manager=session_manager,
        pending_changes=pending_changes,
        audit_log=audit_log,
    )

    if task:
        fn = getattr(dw, task, None)
        if fn is None:
            click.echo(f"Unknown task '{task}'. Valid tasks: {', '.join(DREAM_TASKS)}", err=True)
            return
        click.echo(f"Running {task}...")
        await fn()
        click.echo(f"✓ {task} complete.")
    else:
        click.echo("Running full dream cycle (5 tasks)...")
        await dw.run_full_cycle()
        click.echo("✓ Dream cycle complete.")


# ── wake ───────────────────────────────────────────────────────────────────────


@cli.command()
def wake():
    """Request an immediate transition to wake mode (sends WoL, starts R710)."""
    result = _api_call("POST", "/api/mode", {"mode": "wake"})
    click.echo(f"Mode change requested: {result}")


# ── pending ────────────────────────────────────────────────────────────────────


@cli.command()
def pending():
    """List all pending skill-config changes awaiting review."""
    from app.storage.pending_changes import list_pending

    changes = list_pending()
    if not changes:
        click.echo("No pending changes.")
        return

    click.echo(f"{'ID':<10} {'Type':<12} {'Author':<12} {'Proposed':<22} {'Title'}")
    click.echo("-" * 80)
    for c in changes:
        proposed = c.get("proposed_at", "")[:19].replace("T", " ")
        click.echo(
            f"{c.get('id', ''):<10} "
            f"{c.get('change_type', ''):<12} "
            f"{c.get('author', ''):<12} "
            f"{proposed:<22} "
            f"{c.get('title', '')}"
        )
    click.echo()
    click.echo(f"  {len(changes)} pending change(s).  Use 'approve ID' or 'reject ID' to review.")


# ── approve ────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("change_id")
def approve(change_id: str):
    """Approve a pending change by its ID and apply it to the target file."""
    from app.storage.pending_changes import approve as _approve

    ok = _approve(change_id, approved_by="cli")
    if ok:
        click.echo(f"✓ Change '{change_id}' approved and applied.")
    else:
        click.echo(f"✗ Change '{change_id}' not found in pending queue.", err=True)
        sys.exit(1)


# ── reject ─────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("change_id")
@click.option("--reason", default="", help="Reason for rejection (stored with the change).")
def reject(change_id: str, reason: str):
    """Reject a pending change by its ID."""
    from app.storage.pending_changes import reject as _reject

    ok = _reject(change_id, rejected_by="cli", reason=reason)
    if ok:
        click.echo(f"✓ Change '{change_id}' rejected.")
    else:
        click.echo(f"✗ Change '{change_id}' not found in pending queue.", err=True)
        sys.exit(1)


# ── journal ────────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--days", default=3, show_default=True, help="Number of days of journal to show.")
def journal(days: int):
    """Display recent journal entries."""
    from app.skills.memory import MemorySkill

    result = MemorySkill().read_recent_journal(n_days=days)
    if result.success and result.output:
        click.echo_via_pager(result.output)
    else:
        click.echo("No journal entries found.")


# ── test-skill ─────────────────────────────────────────────────────────────────


@cli.command("test-skill")
@click.argument("skill_name")
@click.option(
    "--args",
    "args_json",
    default="{}",
    help='JSON string of arguments to pass, e.g. \'{"query": "board minutes"}\'',
)
def test_skill(skill_name: str, args_json: str):
    """Run a skill directly and print its result."""
    try:
        kwargs = json.loads(args_json)
    except json.JSONDecodeError as exc:
        click.echo(f"Invalid JSON for --args: {exc}", err=True)
        sys.exit(1)

    _run(_run_skill(skill_name, kwargs))


async def _run_skill(skill_name: str, kwargs: dict) -> None:
    _SKILL_MAP = {
        "search":        ("app.skills.search",        "SearchSkill"),
        "indexer":       ("app.skills.indexer",       "IndexerSkill"),
        "writer":        ("app.skills.writer",        "WriterSkill"),
        "memory":        ("app.skills.memory",        "MemorySkill"),
        "skill_builder": ("app.skills.skill_builder", "SkillBuilderSkill"),
    }

    if skill_name not in _SKILL_MAP:
        click.echo(
            f"Unknown skill '{skill_name}'. "
            f"Available: {', '.join(sorted(_SKILL_MAP))}",
            err=True,
        )
        sys.exit(1)

    module_path, class_name = _SKILL_MAP[skill_name]
    import importlib
    module = importlib.import_module(module_path)
    SkillClass = getattr(module, class_name)
    skill = SkillClass()

    click.echo(f"Running skill '{skill_name}' with args: {kwargs}")
    result = await skill.run(**kwargs)

    if result.success:
        click.echo(f"\n✓ Success")
        if result.output is not None:
            click.echo(f"\n{result.output}")
        if result.metadata:
            click.echo(f"\nMetadata: {json.dumps(result.metadata, indent=2)}")
    else:
        click.echo(f"\n✗ Failed: {result.error}", err=True)
        sys.exit(1)


# ── new-skill ──────────────────────────────────────────────────────────────────


@cli.command("new-skill")
@click.argument("description")
def new_skill(description: str):
    """
    Draft a new skill from a plain-English description.

    Uses the LLM to produce a SKILL.md spec and optional Python file,
    saved to skills_config/drafts/. Nothing is auto-installed.
    """
    _run(_draft_skill(description))


async def _draft_skill(description: str) -> None:
    from app.skills.skill_builder import SkillBuilderSkill

    click.echo(f"Drafting skill from description: {description!r}")
    click.echo("(Calling LLM — this may take a moment...)")
    result = await SkillBuilderSkill().run(description=description)

    if result.success:
        click.echo(f"\n✓ Skill draft created\n")
        click.echo(result.output)
    else:
        click.echo(f"\n✗ Failed: {result.error}", err=True)
        sys.exit(1)


# ── add-user ───────────────────────────────────────────────────────────────────


@cli.command("add-user")
@click.argument("username")
@click.argument("role", type=click.Choice(["admin", "staff", "trustee"]))
@click.password_option(help="Password for the new user.")
def add_user(username: str, role: str, password: str):
    """Add a new user to the local user database."""
    from app.auth.auth import create_user

    try:
        user = create_user(username, password, role=role)
        click.echo(f"✓ User '{user.username}' created with role '{user.role}'.")
    except ValueError as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)


# ── list-docs ──────────────────────────────────────────────────────────────────


@cli.command("list-docs")
@click.option("--type", "doc_type", default=None, help="Filter by document type.")
def list_docs(doc_type: Optional[str]):
    """List all indexed documents in the vector store."""
    from app.storage.vector_store import vector_store

    try:
        docs = vector_store.list_documents()
    except Exception as exc:
        click.echo(f"Could not read ChromaDB: {exc}", err=True)
        click.echo("Is the CHROMA_PATH set correctly in .env?", err=True)
        sys.exit(1)

    if doc_type:
        docs = [d for d in docs if d.get("document_type") == doc_type]

    if not docs:
        click.echo("No documents indexed." if not doc_type else f"No documents of type '{doc_type}'.")
        return

    click.echo(f"{'Type':<14} {'Chunks':>6}  {'Indexed':<22}  Source file")
    click.echo("-" * 80)
    for d in sorted(docs, key=lambda x: x.get("indexed_at", "")):
        indexed = d.get("indexed_at", "")[:19].replace("T", " ")
        click.echo(
            f"{d.get('document_type', 'other'):<14} "
            f"{d.get('chunk_count', 0):>6}  "
            f"{indexed:<22}  "
            f"{d.get('source_file', '')}"
        )
    click.echo()
    click.echo(f"  {len(docs)} document(s) indexed.")


# ── skills ─────────────────────────────────────────────────────────────────────


@cli.command()
def skills():
    """List all available skills with their descriptions."""
    _SKILLS = [
        ("search",        "Semantic document search — query the indexed archive"),
        ("indexer",       "Index documents from WATCHED_FOLDER into ChromaDB"),
        ("writer",        "Draft documents using org templates"),
        ("memory",        "Read/update agent memory, journal, and task context"),
        ("skill_builder", "Draft a new skill from a plain-English description"),
        ("dream_worker",  "Run overnight dream-mode analysis tasks"),
    ]
    click.echo("\nAvailable skills:\n")
    for name, desc in _SKILLS:
        click.echo(f"  {name:<20} {desc}")
    click.echo()
    click.echo("Run a skill:   cdcn-agent test-skill SKILL_NAME [--args '{\"key\": \"val\"}']")
    click.echo("Draft a skill: cdcn-agent new-skill \"description of what it should do\"")
    click.echo()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
