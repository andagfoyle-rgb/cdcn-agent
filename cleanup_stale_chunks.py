"""
Remove ChromaDB chunks whose source_file no longer exists.

This cleans up stale entries left over from the folder migration —
chunks that still reference flat paths like 'CDCN_Minutes.pdf' when
the file has been moved to 'minutes/CDCN_Minutes.pdf'.
"""
import sys
import os

os.chdir("/opt/cdcn-agent")
sys.path.insert(0, "/opt/cdcn-agent")

os.environ.setdefault("WATCHED_FOLDER", "/var/lib/cdcn-agent/documents")
os.environ.setdefault("CHROMA_PATH", "/var/lib/cdcn-agent/chroma")
os.environ.setdefault("MEMORY_PATH", "/var/lib/cdcn-agent/memory")
os.environ.setdefault("AUDIT_LOG_PATH", "/var/lib/cdcn-agent/cdcn_audit.db")
os.environ.setdefault("ALLOW_PUBLIC_BIND", "true")

from pathlib import Path
import chromadb
from chromadb.config import Settings as ChromaSettings

DOCS_DIR = Path("/var/lib/cdcn-agent/documents")
CHROMA_PATH = "/var/lib/cdcn-agent/chroma"

client = chromadb.PersistentClient(
    path=CHROMA_PATH,
    settings=ChromaSettings(anonymized_telemetry=False),
)
col = client.get_or_create_collection("cdcn_documents")

print(f"Total chunks before cleanup: {col.count()}")

# Fetch all chunk IDs and their source_file metadata
results = col.get(include=["metadatas"])
ids = results["ids"]
metas = results["metadatas"]

stale_ids = []
for chunk_id, meta in zip(ids, metas):
    src = meta.get("source_file", "")
    if not src:
        continue
    # Check if the file actually exists (direct join or rglob)
    direct = DOCS_DIR / src
    if direct.exists():
        continue
    # Try bare filename search
    bare = Path(src).name
    found = list(DOCS_DIR.rglob(bare))
    if found:
        continue
    # File not found at old or new path — stale chunk
    stale_ids.append(chunk_id)

print(f"Stale chunks to remove: {len(stale_ids)}")

if stale_ids:
    # Delete in batches of 500
    batch_size = 500
    removed = 0
    for i in range(0, len(stale_ids), batch_size):
        batch = stale_ids[i : i + batch_size]
        col.delete(ids=batch)
        removed += len(batch)
    print(f"Removed {removed} stale chunks")
else:
    print("No stale chunks found")

print(f"Total chunks after cleanup: {col.count()}")
