"""
Simple file-based key-value store backed by the memory directory.
"""
import json
from pathlib import Path

from app.config import settings


class FileStore:
    def __init__(self, namespace: str = "general") -> None:
        self._root = Path(settings.memory_path) / namespace
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._root / f"{key}.json"

    def set(self, key: str, value) -> None:
        self._path(key).write_text(json.dumps(value, indent=2))

    def get(self, key: str, default=None):
        p = self._path(key)
        if not p.exists():
            return default
        return json.loads(p.read_text())

    def delete(self, key: str) -> bool:
        p = self._path(key)
        if p.exists():
            p.unlink()
            return True
        return False

    def keys(self) -> list[str]:
        return [f.stem for f in self._root.glob("*.json")]
