"""Non-destructive restore of a verified backup.

Usage (stop the API first):
  python -m app.restore <backup_dir> [--target path/to/oncoscope.sqlite3] [--documents path/to/documents] [--recovery-key]

1. The backup is verified against its manifest (SQLite integrity, audit chain, table hashes, every document hash). An
   encrypted backup (M3.1-C) is verified with the database key derived from the backup's own key vault, which is opened
   with the owner's passphrase or recovery key. A wrong credential stops here, before anything is written.
2. If the target database already exists it is first backed up (and verified) beside it; nothing is deleted.
3. The database is copied through SQLite's backup API (encrypted backups stay encrypted: both sides use the same key);
   documents are copied only where missing. An existing document file with the same name but different bytes stops the
   restore rather than being overwritten. A key vault is copied only into a target that has none.
4. The restored copy is verified against the manifest again; any difference is reported and the exit code is 1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from .backup import DATABASE_FILE, VAULT_FILE, backup, build_manifest, compare_manifests, copy_database, verify_backup
from .db import DatabaseLocked, DatabaseRefused, database_path, document_dir, file_kind, vault_path


class RestoreError(RuntimeError):
    pass


def restore(backup_dir: Path, target_db: Path | None = None, target_docs: Path | None = None,
            key: bytes | bytearray | None = None) -> dict:
    check = verify_backup(backup_dir, key)
    if not check["ok"]:
        raise RestoreError("Backup failed verification; nothing was restored: " + "; ".join(check["problems"]))
    target_db = target_db or database_path()
    target_docs = target_docs or (document_dir() if target_db == database_path() else target_db.parent / "documents")
    target_vault = vault_path() if target_db == database_path() else target_db.parent / VAULT_FILE
    source_vault = backup_dir / VAULT_FILE
    if source_vault.exists() and target_vault.exists() and target_vault.read_bytes() != source_vault.read_bytes():
        # Never replace a key vault: the target's may protect data this backup does not contain.
        raise RestoreError(f"The target already has a different key vault ({target_vault}); nothing was restored. "
                           "Restore into an empty folder, or move that vault away deliberately.")

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
        try:   # the same vault (checked above) means the same database key
            preserved = backup(target_db.parent / "pre-restore-backups", db_path=target_db, doc_root=target_docs,
                               key=key if file_kind(target_db) == "encrypted" else None)
        except SystemExit as exc:
            raise RestoreError(f"The existing target could not be backed up first, so nothing was restored: {exc}") from None
    target_db.parent.mkdir(parents=True, exist_ok=True)
    staging = target_db.with_name(target_db.name + ".restoring")
    staging.unlink(missing_ok=True)
    try:
        copy_database(backup_dir / DATABASE_FILE, staging, key)
    except (DatabaseRefused, DatabaseLocked) as exc:
        staging.unlink(missing_ok=True)
        raise RestoreError(f"The backup's database could not be copied; nothing was restored: {exc}") from None
    os.replace(staging, target_db)
    copied = 0
    for f in files:
        destination = target_docs / f.relative_to(source_docs)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, destination)
            copied += 1

    if source_vault.exists() and not target_vault.exists():
        target_vault.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_vault, target_vault)
    differences = compare_manifests(check["manifest"], build_manifest(target_db, target_docs, target_vault, key))
    return {"restored_to": str(target_db), "documents_dir": str(target_docs), "documents_copied": copied,
            "preserved_previous": str(preserved) if preserved else None, "consistent": not differences,
            "encrypted": file_kind(target_db) == "encrypted",
            "differences": differences, "tables": {t: v["rows"] for t, v in check["manifest"]["tables"].items()}}


def main(argv: list[str] | None = None) -> int:
    from .credentials import CredentialError, database_key_from_vault
    parser = argparse.ArgumentParser(description="Verify and restore an OncoScope synthetic backup")
    parser.add_argument("backup_dir", type=Path)
    parser.add_argument("--target", type=Path, default=None)
    parser.add_argument("--documents", type=Path, default=None)
    parser.add_argument("--recovery-key", action="store_true", help="unlock with the recovery key instead of the passphrase")
    args = parser.parse_args(argv)
    key = None
    try:
        if file_kind(args.backup_dir / DATABASE_FILE) == "encrypted":
            key = database_key_from_vault(args.backup_dir / VAULT_FILE, use_recovery_key=args.recovery_key)
        result = restore(args.backup_dir, args.target, args.documents, key)
    except (RestoreError, CredentialError) as exc:
        print(json.dumps({"consistent": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["consistent"] else 1


if __name__ == "__main__":
    sys.exit(main())
