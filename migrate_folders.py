"""
Migration script: move documents to subdirectories based on doc_type.
Updates document_metadata.filepath and indexed_files.path in the DB.
"""
import shutil
import sqlite3
from pathlib import Path

DB_PATH = "/var/lib/cdcn-agent/indexer.db"
DOCS_DIR = Path("/var/lib/cdcn-agent/documents")

# Map doc_type -> subdir
TYPE_TO_SUBDIR = {
    "minutes": "minutes",
    "agenda": "agendas",
    "policy": "policies",
    "policies": "policies",
    "funding": "funding",
    "report": "reports",
    "plan": "plans",
    "draft": "drafts",
    "other": "other",
    "constitution": "other",
    "journal": "other",
    # indexed_files uses 'application' for funding
    "application": "funding",
}

conn = sqlite3.connect(DB_PATH)

# Get all rows from document_metadata
rows = conn.execute(
    "SELECT rowid, filename, filepath, doc_type FROM document_metadata"
).fetchall()

print(f"Found {len(rows)} rows in document_metadata")

moved = 0
skipped = 0
errors = 0

for rowid, filename, filepath, doc_type in rows:
    subdir = TYPE_TO_SUBDIR.get(doc_type, "other")
    old_path = Path(filepath) if filepath else DOCS_DIR / filename

    # If file is already in a subdir, skip
    if old_path.parent != DOCS_DIR:
        print(f"  SKIP (already moved?): {filename} -> {old_path.parent}")
        skipped += 1
        continue

    new_path = DOCS_DIR / subdir / filename

    if not old_path.exists():
        print(f"  WARN: source file not found: {old_path}")
        # Still update the DB path so it points to right place
        conn.execute(
            "UPDATE document_metadata SET filepath = ? WHERE rowid = ?",
            (str(new_path), rowid)
        )
        errors += 1
        continue

    try:
        shutil.move(str(old_path), str(new_path))
        conn.execute(
            "UPDATE document_metadata SET filepath = ? WHERE rowid = ?",
            (str(new_path), rowid)
        )
        print(f"  MOVED: {filename} -> {subdir}/")
        moved += 1
    except Exception as e:
        print(f"  ERROR moving {filename}: {e}")
        errors += 1

# Also update indexed_files.path
print("\nUpdating indexed_files.path ...")
idx_rows = conn.execute("SELECT rowid, path, document_type FROM indexed_files").fetchall()
print(f"Found {len(idx_rows)} rows in indexed_files")

for rowid, path, doc_type in idx_rows:
    old_path = Path(path)
    if old_path.parent != DOCS_DIR:
        print(f"  SKIP indexed_files (already moved?): {old_path.name}")
        continue
    filename = old_path.name
    subdir = TYPE_TO_SUBDIR.get(doc_type, "other")
    new_path = DOCS_DIR / subdir / filename
    conn.execute(
        "UPDATE indexed_files SET path = ? WHERE rowid = ?",
        (str(new_path), rowid)
    )
    print(f"  UPDATED indexed_files: {filename} -> {subdir}/")

conn.commit()
conn.close()

print(f"\nDone. Moved: {moved}, Skipped: {skipped}, Errors: {errors}")
print("\nNew directory contents:")
for subdir in ["minutes", "agendas", "policies", "funding", "reports", "plans", "drafts", "other"]:
    files = list((DOCS_DIR / subdir).iterdir())
    print(f"  {subdir}/: {len(files)} files")
