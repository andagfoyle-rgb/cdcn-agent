"""
Pending changes store — proposed edits to skill_config files awaiting review.

File layout
───────────
  PENDING_CHANGES_PATH/
    {id}.json   — metadata (id, title, author, proposed_at, status,
                             change_type, target_file)
    {id}.diff   — proposed content / diff text
    applied/    — moved here after approve()
    rejected/   — moved here after reject()

Change types
────────────
  agents    — changes to skills_config/memory/agents.md
  style     — changes to skills_config/memory/style_guide.md
  templates — changes to skills_config/templates/
  general   — anything else

Approval
────────
  approve(change_id, approved_by):
    1. Read target_file path from JSON metadata.
    2. If the diff begins with a markdown heading (##) that exists in the
       target file, replace that section.  Otherwise append.
    3. Move JSON + diff to applied/.
    4. Invalidate the system-prompt cache so the next request picks up the
       change immediately.
    5. Log to audit_log.

The module exposes both a class (PendingChangesManager) and module-level
functions so existing callers (state_manager, dream_worker) continue to work.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import settings


def _pending_dir() -> Path:
    return Path(settings.pending_changes_path)


def _applied_dir() -> Path:
    return _pending_dir() / "applied"


def _rejected_dir() -> Path:
    return _pending_dir() / "rejected"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_target_path(target_file: str) -> Path:
    """Resolve target_file and ensure it stays within skills_config/.

    Raises ValueError if the resolved path escapes the allowed directory.
    """
    allowed_root = (Path.cwd() / "skills_config").resolve()
    # Resolve the path relative to cwd (matching original behaviour)
    raw = Path(target_file)
    target_path = (raw if raw.is_absolute() else Path.cwd() / raw).resolve()
    # Block any path that doesn't land inside skills_config/
    if not (str(target_path) + "/").startswith(str(allowed_root) + "/"):
        raise ValueError(
            f"Path traversal blocked: {target_file!r} resolves outside skills_config/"
        )
    return target_path


# ── Section-level patch helper ─────────────────────────────────────────────────


def _apply_patch(current: str, proposed: str) -> str:
    """
    Merge proposed text into current file content.

    If proposed starts with a markdown heading (## …) that already exists
    in current, replace that section (up to the next same-level heading or
    EOF).  Otherwise append proposed after a blank line.
    """
    # Find the first ## heading in the proposed text
    m = re.match(r"(#{1,3}\s+.+)", proposed.lstrip())
    if not m:
        # No heading — just append
        return current.rstrip() + "\n\n" + proposed.strip() + "\n"

    heading = m.group(1).rstrip()
    level = len(heading) - len(heading.lstrip("#"))
    heading_pattern = re.compile(
        r"^" + re.escape(heading) + r"\s*$", re.MULTILINE
    )

    if not heading_pattern.search(current):
        # Heading not found in target — append
        return current.rstrip() + "\n\n" + proposed.strip() + "\n"

    # Replace from the matching heading to the next heading of equal or higher level
    next_heading_re = re.compile(r"^#{1," + str(level) + r"}\s", re.MULTILINE)
    lines = current.splitlines(keepends=True)

    # Find start line index
    start_idx = next(
        i for i, line in enumerate(lines) if heading_pattern.match(line.rstrip())
    )
    # Find end line index (next heading of same/higher level after start)
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        if next_heading_re.match(lines[i]):
            end_idx = i
            break

    new_lines = lines[:start_idx] + [proposed.strip() + "\n"] + lines[end_idx:]
    return "".join(new_lines)


# ── PendingChangesManager ─────────────────────────────────────────────────────


class PendingChangesManager:
    """Manages the full lifecycle of pending skill_config changes.

    Parameters
    ----------
    pending_dir:
        Override the directory used for pending changes (defaults to
        ``settings.pending_changes_path``).
    memory_skill:
        Optional reference to the MemorySkill instance (currently unused,
        kept for API compatibility with callers that pass it).
    """

    def __init__(
        self,
        pending_dir: Optional[str] = None,
        memory_skill=None,  # kept for API compat
    ) -> None:
        self._pending_dir: Optional[Path] = Path(pending_dir) if pending_dir else None
        # memory_skill stored for potential future use
        self._memory_skill = memory_skill

    def _dir(self) -> Path:
        return self._pending_dir if self._pending_dir is not None else _pending_dir()

    def _applied(self) -> Path:
        return self._dir() / "applied"

    def _rejected(self) -> Path:
        return self._dir() / "rejected"

    # ── Write operations ──────────────────────────────────────────────────────

    def save_proposal(
        self,
        filename: str,
        content: str,
        change_type: str = "general",
        target_file: str = "",
    ) -> str:
        """Write a raw proposal as a .md file.  Returns the file path string."""
        self._dir().mkdir(parents=True, exist_ok=True)
        path = self._dir() / filename
        path.write_text(content, encoding="utf-8")
        return str(path)

    def propose(
        self,
        title: str,
        diff: str,
        author: str = "agent",
        change_type: str = "general",
        target_file: str = "",
    ) -> str:
        """Write a new pending change and return its ID."""
        if target_file:
            _validate_target_path(target_file)  # fail fast on bad paths
        cid = str(uuid.uuid4())[:8]
        self._dir().mkdir(parents=True, exist_ok=True)
        meta = {
            "id": cid,
            "title": title,
            "author": author,
            "proposed_at": _now_iso(),
            "status": "pending",
            "change_type": change_type,
            "target_file": target_file,
        }
        (self._dir() / f"{cid}.json").write_text(json.dumps(meta, indent=2))
        (self._dir() / f"{cid}.diff").write_text(diff)
        return cid

    def approve(self, change_id: str, approved_by: str = "") -> bool:
        """
        Apply the proposed diff to its target file, move to applied/, and log.

        Returns True if the change was found and applied, False otherwise.
        """
        meta_file = self._dir() / f"{change_id}.json"
        diff_file = self._dir() / f"{change_id}.diff"
        if not meta_file.exists():
            return False

        meta = json.loads(meta_file.read_text())
        diff_text = diff_file.read_text() if diff_file.exists() else ""
        target_file = meta.get("target_file", "")

        # Apply patch to target file if one is specified
        if target_file:
            target_path = _validate_target_path(target_file)
            if target_path.exists():
                current = target_path.read_text()
                patched = _apply_patch(current, diff_text)
                target_path.write_text(patched)
            else:
                # Target doesn't exist yet — create it
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(diff_text)

        # Move files to applied/
        self._applied().mkdir(parents=True, exist_ok=True)
        meta["status"] = "approved"
        meta["approved_by"] = approved_by
        meta["approved_at"] = _now_iso()
        (self._applied() / meta_file.name).write_text(json.dumps(meta, indent=2))
        meta_file.unlink()
        if diff_file.exists():
            diff_file.rename(self._applied() / diff_file.name)

        # Invalidate system-prompt cache so the change takes effect immediately
        _invalidate_prompt_cache()

        # Log
        from app.storage.audit_log import log_pending_action_sync
        log_pending_action_sync(
            filename=f"{change_id}.diff",
            action="approved",
            user_id=approved_by,
        )
        return True

    def reject(self, change_id: str, rejected_by: str = "", reason: str = "") -> bool:
        """
        Append the rejection reason to the diff file, move to rejected/, and log.

        Returns True if the change was found, False otherwise.
        """
        meta_file = self._dir() / f"{change_id}.json"
        diff_file = self._dir() / f"{change_id}.diff"
        if not meta_file.exists():
            return False

        meta = json.loads(meta_file.read_text())
        meta["status"] = "rejected"
        meta["rejected_by"] = rejected_by
        meta["rejected_at"] = _now_iso()
        if reason:
            meta["rejection_reason"] = reason

        self._rejected().mkdir(parents=True, exist_ok=True)
        (self._rejected() / meta_file.name).write_text(json.dumps(meta, indent=2))
        meta_file.unlink()

        if diff_file.exists():
            # Append rejection note to the diff before archiving
            note = f"\n\n---\nRejected by {rejected_by} at {_now_iso()}"
            if reason:
                note += f"\nReason: {reason}"
            archived_diff = self._rejected() / diff_file.name
            archived_diff.write_text(diff_file.read_text() + note)
            diff_file.unlink()

        from app.storage.audit_log import log_pending_action_sync
        log_pending_action_sync(
            filename=f"{change_id}.diff",
            action="rejected",
            user_id=rejected_by,
            reason=reason,
        )
        return True

    def apply_approved(self) -> list[str]:
        """
        Apply all pending changes with status 'approved' (set by external review).
        Called during wake transition.  Returns list of applied change IDs.
        """
        applied: list[str] = []
        for change in self.list_pending():
            if change.get("status") == "approved":
                cid = change["id"]
                if self.approve(cid, approved_by="wake_transition"):
                    applied.append(cid)
        return applied

    # ── Read operations ───────────────────────────────────────────────────────

    def list_pending(self) -> list[dict]:
        """
        Return metadata for all pending (not yet approved/rejected) changes,
        sorted by proposed_at ascending.

        Handles both the structured JSON+diff format and raw .md proposal files.
        """
        results: list[dict] = []
        d = self._dir()
        if not d.exists():
            return results

        # Structured format: .json metadata + .diff content
        for f in d.glob("*.json"):
            try:
                meta = json.loads(f.read_text())
                diff_file = d / f"{meta['id']}.diff"
                preview = ""
                if diff_file.exists():
                    preview = diff_file.read_text()[:200]
                meta["preview"] = preview
                results.append(meta)
            except Exception:
                pass

        # Raw .md format written by dream_worker
        for f in d.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
                # Parse TARGET_FILE and CHANGE_TYPE from header lines
                target_file = ""
                change_type = "general"
                for line in content.splitlines()[:5]:
                    if line.startswith("TARGET_FILE:"):
                        target_file = line.split(":", 1)[1].strip()
                    elif line.startswith("CHANGE_TYPE:"):
                        change_type = line.split(":", 1)[1].strip()
                results.append({
                    "id": f.stem,
                    "title": f.stem,
                    "author": "agent",
                    "proposed_at": datetime.fromtimestamp(
                        f.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "status": "pending",
                    "change_type": change_type,
                    "target_file": target_file,
                    "preview": content[:200],
                })
            except Exception:
                pass

        return sorted(results, key=lambda x: x.get("proposed_at", ""))

    def get_diff(self, change_id: str) -> dict:
        """
        Return both the current content of the target file and the proposed
        content (diff text).

        Keys: current_content, proposed_content, target_file, title.
        Handles both structured JSON+diff format and raw .md files.
        """
        d = self._dir()
        # Strip .md extension if caller passed a full filename
        stem = change_id[:-3] if change_id.endswith(".md") else change_id

        # Try structured format first
        meta_file = d / f"{stem}.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            diff_file = d / f"{stem}.diff"
            proposed = diff_file.read_text() if diff_file.exists() else ""
            target_file = meta.get("target_file", "")
        else:
            # Fall back to raw .md file
            md_file = d / f"{stem}.md"
            if not md_file.exists():
                return {"current_content": "", "proposed_content": "", "target_file": "", "title": ""}
            proposed = md_file.read_text(encoding="utf-8")
            target_file = ""
            for line in proposed.splitlines()[:5]:
                if line.startswith("TARGET_FILE:"):
                    target_file = line.split(":", 1)[1].strip()

        current = ""
        if target_file:
            try:
                tp = _validate_target_path(target_file)
            except ValueError:
                tp = None
            if tp and tp.exists():
                current = tp.read_text()

        return {
            "current_content": current,
            "proposed_content": proposed,
            "target_file": target_file,
            "title": stem,
        }


# ── Module-level singleton ─────────────────────────────────────────────────────

_manager = PendingChangesManager()


def _invalidate_prompt_cache() -> None:
    """Clear the system-prompt TTL cache in gateway/router so changes take effect."""
    try:
        from app.gateway.prompt_builder import _PROMPT_CACHE
        _PROMPT_CACHE.clear()
    except Exception:
        pass


# ── Module-level backward-compat functions ─────────────────────────────────────


def propose(
    title: str,
    diff: str,
    author: str = "agent",
    change_type: str = "general",
    target_file: str = "",
) -> str:
    return _manager.propose(
        title=title, diff=diff, author=author,
        change_type=change_type, target_file=target_file,
    )


def apply(change_id: str) -> bool:
    """Backward-compat: approve a change by ID."""
    return _manager.approve(change_id)


def reject(change_id: str, rejected_by: str = "", reason: str = "") -> bool:
    return _manager.reject(change_id, rejected_by=rejected_by, reason=reason)


def list_pending() -> list[dict]:
    return _manager.list_pending()


def get_diff(change_id: str) -> dict:
    return _manager.get_diff(change_id)


def approve(change_id: str, approved_by: str = "") -> bool:
    return _manager.approve(change_id, approved_by=approved_by)


def apply_approved() -> list[str]:
    return _manager.apply_approved()
