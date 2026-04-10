"""
Cross-interface identity linking.

Maintains a JSON file mapping interface-specific user IDs (e.g. "discord:123456")
to a canonical user ID (e.g. "web:alice").  When a Discord user is confirmed as
the same person as a web user, both IDs resolve to the same canonical identity,
giving them a shared session and conversation history.

File layout:
    data/memory/identity_map.json
    {
        "discord:123456": "web:alice",
        "discord:789012": "web:bob"
    }

The canonical ID is always the web username (since web accounts are explicitly
created with known usernames).  Discord IDs that have no confirmed link are
not present in the map and continue to use their own namespaced identity.
"""
import json
import logging
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)


def _map_path() -> Path:
    return Path(settings.memory_path) / "identity_map.json"


def _load() -> dict[str, str]:
    p = _map_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to read identity map: %s", exc)
        return {}


def _save(mapping: dict[str, str]) -> None:
    p = _map_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(mapping, indent=2))
        tmp.rename(p)
    except OSError as exc:
        log.error("Failed to write identity map: %s", exc)
        tmp.unlink(missing_ok=True)


def resolve(user_id: str) -> str:
    """Return the canonical user ID, or the original if no link exists."""
    mapping = _load()
    return mapping.get(user_id.strip().lower(), user_id)


def link(source_id: str, canonical_id: str) -> None:
    """Record that source_id is the same person as canonical_id."""
    mapping = _load()
    mapping[source_id.strip().lower()] = canonical_id.strip().lower()
    _save(mapping)
    log.info("Identity linked: %s -> %s", source_id, canonical_id)


def unlink(source_id: str) -> None:
    """Remove an identity link."""
    mapping = _load()
    key = source_id.strip().lower()
    if key in mapping:
        del mapping[key]
        _save(mapping)
        log.info("Identity unlinked: %s", source_id)


def get_all_links() -> dict[str, str]:
    """Return a copy of the full identity map."""
    return _load()


def is_linked(user_id: str) -> bool:
    """Check if a user ID has an existing link."""
    return user_id.strip().lower() in _load()
