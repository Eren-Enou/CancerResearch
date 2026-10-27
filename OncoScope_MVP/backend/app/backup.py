"""Non-destructive local backup with a verification manifest: `python -m app.backup [target_dir] [--recovery-key]`.

A backup directory holds a consistent database snapshot, the content-addressed document store, the key vault, and
`manifest.json` (schema version, per-table row counts and content hashes, audit-chain status, every document's hash
check). Restores (`python -m app.restore`) verify against this manifest before and after copying.

Encrypted databases (protected mode, M3.1-C) are snapshotted through SQLite's backup API between two connections keyed
with the same database key, so the copy is encrypted page by page and is never written decrypted. Making or verifying
such a backup needs the owner's passphrase or recovery key (the database key is derived from the vault). Documents and
the manifest are still plaintext until M3.1-D.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import vault as key_vault
from .db import (
    SQLCIPHER_VERSION, DatabaseLocked, DatabaseRefused, app_mode, audit_head, database_path, document_dir, file_kind,
    now_utc, open_encrypted, sqlite, vault_path, verify_audit_chain,
)

MANIFEST_TABLES = ("patients", "sources", "events", "labs", "treatments", "studies", "measurements", "extractions",
                   "audit_log", "schema_migrations", "local_owner")
DATABASE_FILE = "oncoscope.sqlite3"


def _document_path(root: Path, sha256: str) -> Path:
    return root / sha256[:2] / sha256


STORE_ENTRY = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{64}$")


def unexpected_store_files(doc_root: Path) -> list[str]:
    """Anything in a document store that is not '<2 hex>/<64 hex>' whose bytes hash to its own name."""
    problems = []
    if not doc_root.exists():
        return problems
    for path in sorted(doc_root.rglob("*")):
        rel = path.relative_to(doc_root).as_posix()
        if path.is_symlink():
            problems.append(f"{rel}: symbolic link")
        elif path.is_dir():
            if not re.fullmatch(r"[0-9a-f]{2}", rel):
                problems.append(f"{rel}/: unexpected folder")
        elif not STORE_ENTRY.match(rel):
            problems.append(f"{rel}: not a content-addressed document file")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != path.name or path.parent.name != path.name[:2]:
            problems.append(f"{rel}: bytes do not match the file's hash name")
    return problems


VAULT_FILE = "oncoscope-vault.json"


def vault_summary(path: Path) -> dict | None:
    """Identifies a key vault file without opening it (it holds only wrapped keys). None when there is no vault."""
    if not path.exists():
        return None
    data = path.read_bytes()
    try:
        doc = key_vault.parse(data)
        ids = {"vault_id": doc["vault_id"], "key_id": doc["key_id"], "valid": True,
               "database_key_fingerprint": doc["database"]["key_fingerprint"] if doc["version"] >= 2 else None}
    except key_vault.VaultError as exc:
        ids = {"vault_id": None, "key_id": None, "valid": False, "problem": exc.message}
    return {"sha256": hashlib.sha256(data).hexdigest(), **ids}


def open_snapshot(db_path: Path, key: bytes | bytearray | None):
    """Read access to a database file: keyed for an encrypted one (refused without the right key), read-only otherwise."""
    if file_kind(db_path) == "encrypted":
        conn = open_encrypted(db_path, key)
    else:
        conn = sqlite.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite.Row
    return conn


def build_manifest(db_path: Path, doc_root: Path, vault_file: Path | None = None,
                   key: bytes | bytearray | None = None) -> dict:
    """Describes a database + document store precisely enough to prove a restore reproduced it."""
    encrypted = file_kind(db_path) == "encrypted"
    conn = open_snapshot(db_path, key)
    try:
        tables = {}
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in MANIFEST_TABLES:
            if table not in existing:
                continue
            order = "version" if table == "schema_migrations" else "id"
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]
            digest = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
            tables[table] = {"rows": len(rows), "sha256": digest}
        documents = {}
        if "sources" in existing:
            for (sha,) in conn.execute("SELECT DISTINCT document_sha256 FROM sources WHERE document_sha256 IS NOT NULL ORDER BY 1"):
                path = _document_path(doc_root, sha)
                actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
                documents[sha] = {"present": actual is not None, "valid": actual == sha}
        return {
            "format": "oncoscope-backup-manifest", "manifest_version": 1, "created_at": now_utc(),
            "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
            "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "audit_chain": verify_audit_chain(conn) if "audit_log" in existing else None,
            "audit_head": audit_head(conn) if "audit_log" in existing else None,
            "tables": tables, "documents": documents,
            "vault": vault_summary(vault_file) if vault_file else None,
            "database_encryption": ({"encrypted": True, "cipher": "sqlcipher4", "sqlcipher_version": SQLCIPHER_VERSION,
                                     "key_fingerprint": key_vault.fingerprint(bytes(key))}
                                    if encrypted else {"encrypted": False}),
        }
    finally:
        conn.close()


def compare_manifests(expected: dict, actual: dict) -> list[str]:
    problems = []
    if actual["integrity_check"] != "ok":
        problems.append(f"SQLite integrity check: {actual['integrity_check']}")
    if actual["schema_version"] != expected["schema_version"]:
        problems.append(f"schema version {actual['schema_version']} != {expected['schema_version']}")
    if not (actual["audit_chain"] or {}).get("valid"):
        problems.append(f"audit hash chain invalid: {actual['audit_chain']}")
    for table, info in expected["tables"].items():
        got = actual["tables"].get(table)
        if got != info:
            problems.append(f"table {table}: expected {info['rows']} rows / {info['sha256'][:12]}, found "
                            f"{(got or {}).get('rows')} rows / {((got or {}).get('sha256') or '')[:12]}")
    if expected.get("audit_head") and actual.get("audit_head") != expected["audit_head"]:
        problems.append(f"audit head {actual.get('audit_head')} != recorded {expected['audit_head']} (entries removed or added)")
    if expected.get("vault") and (actual.get("vault") or {}).get("sha256") != expected["vault"]["sha256"]:
        problems.append("key vault missing or different from the one recorded in the manifest")
    elif expected.get("vault") and not expected["vault"]["valid"]:
        problems.append(f"key vault in the backup is not usable: {expected['vault'].get('problem')}")
    wanted = expected.get("database_encryption") or {"encrypted": False}
    found = actual.get("database_encryption") or {"encrypted": False}
    if wanted["encrypted"] and not found["encrypted"]:
        problems.append("the database copy is not encrypted, but the backup was made from an encrypted database")
    elif wanted["encrypted"] and found.get("key_fingerprint") != wanted.get("key_fingerprint"):
        problems.append("the database is encrypted with a different key than the one recorded in the manifest")
    if wanted["encrypted"] and expected.get("vault") and \
            expected["vault"].get("database_key_fingerprint") != wanted.get("key_fingerprint"):
        problems.append("the key vault in the backup does not belong to its encrypted database")
    for sha in expected["documents"]:
        got = actual["documents"].get(sha)
        if not got or not got["valid"]:
            problems.append(f"document {sha[:12]}: {'missing' if not got or not got['present'] else 'hash mismatch'}")
    return problems


def copy_database(source: Path, destination: Path, key: bytes | bytearray | None) -> None:
    """A consistent copy through SQLite's backup API (safe while the API is running). An encrypted source is copied
    into a destination keyed with the same key, so no decrypted page is ever written."""
    if file_kind(source) == "encrypted":
        src = open_encrypted(source, key)
        dst = open_encrypted(destination, key, create=True)
    else:
        src, dst = sqlite.connect(str(source)), sqlite.connect(str(destination))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if file_kind(source) == "encrypted" and file_kind(destination) != "encrypted":   # belt and braces
        destination.unlink()
        raise RuntimeError("The database copy came out unencrypted; it was deleted")


def _snapshot(db_path: Path, doc_root: Path, target: Path, vault_file: Path | None, key) -> None:
    copy_database(db_path, target / DATABASE_FILE, key)
    if doc_root.exists():
        shutil.copytree(doc_root, target / "documents")
    else:
        (target / "documents").mkdir()
    if vault_file and vault_file.exists():   # wrapped keys only; without it a recovery key cannot open anything
        shutil.copy2(vault_file, target / VAULT_FILE)


def backup(target_dir: Path | None = None, db_path: Path | None = None, doc_root: Path | None = None,
           key: bytes | bytearray | None = None, label: str | None = None) -> Path:
    source = db_path or database_path()
    docs = doc_root or (document_dir() if db_path is None else source.parent / "documents")
    keys = vault_path() if db_path is None else source.parent / VAULT_FILE
    if not source.exists():
        raise SystemExit(f"No database at {source}")
    if file_kind(source) == "encrypted" and key is None:
        raise SystemExit("The database is encrypted; backing it up needs the owner's passphrase or recovery key")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parent = target_dir or source.parent / "backups"
    parent.mkdir(parents=True, exist_ok=True)
    prefix = f"oncoscope-{label}-" if label else "oncoscope-"
    target, n = parent / f"{prefix}{stamp}", 1
    while target.exists():
        n += 1
        target = parent / f"{prefix}{stamp}-{n}"
    target.mkdir()
    try:
        _snapshot(source, docs, target, keys, key)
        manifest = build_manifest(target / DATABASE_FILE, target / "documents", target / VAULT_FILE, key)
    except (DatabaseRefused, DatabaseLocked) as exc:
        shutil.rmtree(target)
        raise SystemExit(f"Backup not made: {exc}") from None
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    problems = compare_manifests(manifest, manifest)
    if problems:
        raise SystemExit("Backup written but it does not verify: " + "; ".join(problems))
    return target


def verify_backup(backup_dir: Path, key: bytes | bytearray | None = None) -> dict:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists() or not (backup_dir / DATABASE_FILE).exists():
        return {"ok": False, "problems": ["backup is missing manifest.json or oncoscope.sqlite3"], "manifest": None}
    try:
        expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "problems": ["manifest.json is damaged"], "manifest": None}
    encrypted = (expected.get("database_encryption") or {}).get("encrypted", False)
    if encrypted and not (backup_dir / VAULT_FILE).exists():
        return {"ok": False, "problems": ["the backup's key vault is missing, so its encrypted database cannot be opened"],
                "manifest": expected}
    if encrypted and key is None:
        return {"ok": False, "problems": ["the database is encrypted; verifying it needs the passphrase or recovery key"],
                "manifest": expected}
    try:
        actual = build_manifest(backup_dir / DATABASE_FILE, backup_dir / "documents", backup_dir / VAULT_FILE, key)
    except (DatabaseRefused, DatabaseLocked) as exc:
        return {"ok": False, "problems": [f"database: {exc}"], "manifest": expected}
    except sqlite.DatabaseError as exc:
        return {"ok": False, "problems": [f"database is damaged or incomplete: {exc}"], "manifest": expected}
    problems = compare_manifests(expected, actual) + [f"documents/{p}" for p in unexpected_store_files(backup_dir / "documents")]
    return {"ok": not problems, "problems": problems, "manifest": expected}


def main(argv: list[str] | None = None) -> int:
    from .credentials import CredentialError, database_key_from_vault
    parser = argparse.ArgumentParser(description="Back up OncoScope's database, documents and key vault (synthetic data)")
    parser.add_argument("target_dir", type=Path, nargs="?", default=None)
    parser.add_argument("--recovery-key", action="store_true", help="unlock with the recovery key instead of the passphrase")
    args = parser.parse_args(argv)
    key = None
    if app_mode() == "protected" and file_kind(database_path()) == "encrypted":
        try:
            key = database_key_from_vault(vault_path(), use_recovery_key=args.recovery_key)
        except CredentialError as exc:
            print(f"Backup not made: {exc}", file=sys.stderr)
            return 1
    print(f"Backup written and verified: {backup(args.target_dir, key=key)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
