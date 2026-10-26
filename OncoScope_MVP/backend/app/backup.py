"""Non-destructive local backup with a verification manifest: `python -m app.backup [target_dir]`.

A backup directory holds a consistent SQLite snapshot, the content-addressed document store, and `manifest.json`
(schema version, per-table row counts and content hashes, audit-chain status, every document's hash check).
Restores (`python -m app.restore`) verify against this manifest before and after copying.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .db import database_path, document_dir, now_utc, verify_audit_chain

MANIFEST_TABLES = ("patients", "sources", "events", "labs", "treatments", "studies", "measurements", "extractions",
                   "audit_log", "schema_migrations")


def _document_path(root: Path, sha256: str) -> Path:
    return root / sha256[:2] / sha256


def build_manifest(db_path: Path, doc_root: Path) -> dict:
    """Describes a database + document store precisely enough to prove a restore reproduced it."""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
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
            "tables": tables, "documents": documents,
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
    for sha in expected["documents"]:
        got = actual["documents"].get(sha)
        if not got or not got["valid"]:
            problems.append(f"document {sha[:12]}: {'missing' if not got or not got['present'] else 'hash mismatch'}")
    return problems


def _snapshot(db_path: Path, doc_root: Path, target: Path) -> None:
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target / "oncoscope.sqlite3")
    try:
        src.backup(dst)  # consistent snapshot even while the API is running
    finally:
        dst.close()
        src.close()
    if doc_root.exists():
        shutil.copytree(doc_root, target / "documents")
    else:
        (target / "documents").mkdir()


def backup(target_dir: Path | None = None, db_path: Path | None = None, doc_root: Path | None = None) -> Path:
    source = db_path or database_path()
    docs = doc_root or (document_dir() if db_path is None else source.parent / "documents")
    if not source.exists():
        raise SystemExit(f"No database at {source}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parent = target_dir or source.parent / "backups"
    parent.mkdir(parents=True, exist_ok=True)
    target, n = parent / f"oncoscope-{stamp}", 1
    while target.exists():
        n += 1
        target = parent / f"oncoscope-{stamp}-{n}"
    target.mkdir()
    _snapshot(source, docs, target)
    manifest = build_manifest(target / "oncoscope.sqlite3", target / "documents")
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    problems = compare_manifests(manifest, manifest)
    if problems:
        raise SystemExit("Backup written but it does not verify: " + "; ".join(problems))
    return target


def verify_backup(backup_dir: Path) -> dict:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists() or not (backup_dir / "oncoscope.sqlite3").exists():
        return {"ok": False, "problems": ["backup is missing manifest.json or oncoscope.sqlite3"], "manifest": None}
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = build_manifest(backup_dir / "oncoscope.sqlite3", backup_dir / "documents")
    problems = compare_manifests(expected, actual)
    return {"ok": not problems, "problems": problems, "manifest": expected}


if __name__ == "__main__":
    print(f"Backup written and verified: {backup(Path(sys.argv[1]) if len(sys.argv) > 1 else None)}")
