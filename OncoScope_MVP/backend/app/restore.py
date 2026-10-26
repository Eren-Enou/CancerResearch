"""Non-destructive restore of a verified backup.

Usage (stop the API first):
  python -m app.restore <backup_dir> [--target path/to/oncoscope.sqlite3] [--documents path/to/documents]

1. The backup is verified against its manifest (SQLite integrity, audit chain, table hashes, every document hash).
2. If the target database already exists it is first backed up (and verified) beside it; nothing is deleted.
3. The database is copied through SQLite's backup API; documents are copied only where missing. An existing document
   file with the same name but different bytes stops the restore rather than being overwritten.
4. The restored copy is verified against the manifest again; any difference is reported and the exit code is 1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from .backup import backup, build_manifest, compare_manifests, verify_backup
from .db import database_path, document_dir


class RestoreError(RuntimeError):
    pass


def restore(backup_dir: Path, target_db: Path | None = None, target_docs: Path | None = None) -> dict:
    check = verify_backup(backup_dir)
    if not check["ok"]:
        raise RestoreError("Backup failed verification; nothing was restored: " + "; ".join(check["problems"]))
    target_db = target_db or database_path()
    target_docs = target_docs or (document_dir() if target_db == database_path() else target_db.parent / "documents")

    conflicts = []
    source_docs = backup_dir / "documents"
    files = [f for f in source_docs.rglob("*") if f.is_file()] if source_docs.exists() else []
    for f in files:
        existing = target_docs / f.relative_to(source_docs)
        if existing.exists() and hashlib.sha256(existing.read_bytes()).hexdigest() != hashlib.sha256(f.read_bytes()).hexdigest():
            conflicts.append(str(existing))
    if conflicts:
        raise RestoreError(f"Target document store has different bytes under the same hash name: {conflicts}; nothing was restored")

    preserved = None
    if target_db.exists():
        preserved = backup(target_db.parent / "pre-restore-backups", db_path=target_db, doc_root=target_docs)
    target_db.parent.mkdir(parents=True, exist_ok=True)
    staging = target_db.with_name(target_db.name + ".restoring")
    src = sqlite3.connect(backup_dir / "oncoscope.sqlite3")
    dst = sqlite3.connect(staging)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(staging, target_db)
    copied = 0
    for f in files:
        destination = target_docs / f.relative_to(source_docs)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, destination)
            copied += 1

    differences = compare_manifests(check["manifest"], build_manifest(target_db, target_docs))
    return {"restored_to": str(target_db), "documents_dir": str(target_docs), "documents_copied": copied,
            "preserved_previous": str(preserved) if preserved else None, "consistent": not differences,
            "differences": differences, "tables": {t: v["rows"] for t, v in check["manifest"]["tables"].items()}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify and restore an OncoScope synthetic backup")
    parser.add_argument("backup_dir", type=Path)
    parser.add_argument("--target", type=Path, default=None)
    parser.add_argument("--documents", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        result = restore(args.backup_dir, args.target, args.documents)
    except RestoreError as exc:
        print(json.dumps({"consistent": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["consistent"] else 1


if __name__ == "__main__":
    sys.exit(main())
