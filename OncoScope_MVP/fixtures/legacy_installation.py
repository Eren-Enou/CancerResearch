"""Builds fictional plaintext installations as earlier OncoScope versions left them, for the M3.1-C migration tests and
the browser demonstration. Fictional data and throwaway passphrases only; never point it at real records.

  python fixtures/legacy_installation.py plaintext <recovery-key-file>   # M3.1-B: schema v5, owner, v1 vault
                                                                          # (passphrase on stdin)
  python fixtures/legacy_installation.py inspect                          # fingerprint (passphrase on stdin if encrypted)
  python fixtures/legacy_installation.py interrupt <step>                 # migrate, stopping right after <step>
  python fixtures/legacy_installation.py sqlite3-read                     # what ordinary SQLite sees

The database location comes from ONCOSCOPE_DB_PATH, as for the application.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Callable

BACKEND = Path(__file__).resolve().parents[1] / "backend"
FICTIONAL = Path(__file__).resolve().parent / "fictional"
sys.path.insert(0, str(BACKEND))

NOW = "2026-10-27T00:00:00Z"
CASE_LABEL = "Fictional Encrypted Case SENTINEL-CASE"
LAB_QUOTE = "CA-125          310      U/mL"


def _attach(client, source_id: int, name: str) -> None:
    r = client.put(f"/api/sources/{source_id}/document", content=(FICTIONAL / name).read_bytes(),
                   headers={"Content-Type": "application/octet-stream", "X-Document-Filename": name})
    assert r.status_code == 201, r.text


def plaintext_case_v5(inspect: Callable | None = None) -> dict:
    """A plaintext schema-v5 database built through the API in demonstration mode (documents, an anchored and corrected
    lab, a status change, an accepted and rejected extraction, an event), exactly as M3.1-B's code wrote one.
    `inspect(client, ids)` runs while that server is still up, to record views for later comparison."""
    from fastapi.testclient import TestClient
    from app import db
    from app.main import app
    os.environ["ONCOSCOPE_MODE"] = "demo"
    migrations, version = db.MIGRATIONS, db.SCHEMA_VERSION
    db.MIGRATIONS, db.SCHEMA_VERSION = migrations[:5], 5
    try:
        with TestClient(app) as c:
            pid = c.post("/api/patients", json={"label": CASE_LABEL}).json()["id"]
            src = c.post("/api/sources", json={"patient_id": pid, "label": "Fictional lab SENTINEL-SOURCE", "kind": "lab",
                                               "source_date": "2026-08-08"}).json()
            _attach(c, src["id"], "lab_2026-08-07_imaginary_valley.txt")
            lab = c.post("/api/labs", json={"patient_id": pid, "source_id": src["id"], "date": "2026-08-07", "test": "CA-125",
                                            "value": 310, "unit": "U/mL", "anchor_quote": LAB_QUOTE}).json()
            assert c.patch(f"/api/records/labs/{lab['id']}", json={"changes": {"value": 311}, "reason": "Fictional typo"}).status_code == 200
            assert c.patch(f"/api/records/labs/{lab['id']}", json={"changes": {"value": 310}, "reason": "Fictional re-check"}).status_code == 200
            assert c.post(f"/api/records/labs/{lab['id']}/status", json={"status": "verified", "reason": "Fictional check"}).status_code == 200
            other = c.post("/api/sources", json={"patient_id": pid, "label": "Fictional portal copy", "kind": "lab",
                                                 "source_date": "2026-09-19"}).json()
            _attach(c, other["id"], "lab_2026-09-18_portal_copy.txt")
            proposals = c.post(f"/api/sources/{other['id']}/extract").json()["created"]
            assert c.post(f"/api/extractions/{proposals[0]['id']}/accept", json={"reason": "Fictional review"}).status_code == 200
            for p in proposals[1:]:
                c.post(f"/api/extractions/{p['id']}/reject", json={"reason": "Fictional rejection"})
            event = c.post("/api/events", json={"patient_id": pid, "source_id": src["id"], "date": "2026-08-09", "kind": "consultation",
                                                 "title": "Fictional SENTINEL-EVENT", "detail": "Fictional detail"})
            assert event.status_code == 201, event.text
            ids = {"pid": pid, "lab": lab["id"], "source": src["id"]}
            if inspect:
                ids["views"] = inspect(c, ids)
    finally:
        db.MIGRATIONS, db.SCHEMA_VERSION = migrations, version
    return ids


def m31b_owner(passphrase: str, *, with_vault: bool = True, vault_version: int = 1) -> str | None:
    """Adds what M3.1-A/B added to a plaintext database: the owner with an Argon2id verifier and, unless
    `with_vault=False` (an M3.1-A installation), a confirmed v1 vault bound to it. Returns the recovery key."""
    from argon2 import PasswordHasher
    from app import db, vault
    conn = db._connect(db.database_path())
    uid = str(uuid.uuid4())
    conn.execute("INSERT INTO local_owner(id, uid, display_name, password_verifier, created_at) VALUES(1,?,?,?,?)",
                 (uid, "Fictional Owner", PasswordHasher().hash(passphrase), NOW))
    recovery_key = None
    marker = db.current_actor.set((f"owner:{uid}", "Fictional Owner"))
    try:
        db.audit(conn, "local_owner", 1, "owner_setup", {"owner_uid": uid})
        if with_vault:
            opened, recovery_key = vault.create(uid, passphrase, NOW, display_name="Fictional Owner", version=vault_version)
            doc = vault.with_recovery_confirmed(opened, NOW)
            vault.write(db.vault_path(), doc, opened.dek, replace_existing=False)
            conn.execute("UPDATE local_owner SET vault_id=?, vault_key_id=? WHERE id=1", (doc["vault_id"], doc["key_id"]))
            db.audit(conn, "local_owner", 1, "vault_initialized", {"vault_id": doc["vault_id"]})
        db.audit(conn, "local_owner", 1, "unlock", {})
    finally:
        db.current_actor.reset(marker)
    conn.commit()
    conn.close()
    return recovery_key


def inspect_database() -> dict:
    """Fingerprint (hashes and counts only) of the database, plaintext or encrypted (passphrase read from stdin)."""
    from app import credentials, db, encrypt_migrate
    path = db.database_path()
    kind = db.file_kind(path)
    if kind == "encrypted":
        key = credentials.database_key_from_vault(db.vault_path(), secret=sys.stdin.readline().rstrip("\r\n"))
        conn = db.open_encrypted(path, key)
    else:
        conn = db._connect(path)
    try:
        found = encrypt_migrate.fingerprint(conn)
        found["patients"] = [r[0] for r in conn.execute("SELECT label FROM patients ORDER BY id")]
    finally:
        conn.close()
    return {"kind": kind, **found}


def main(argv: list[str]) -> int:
    command = argv[0]
    if command == "plaintext":
        os.environ["ONCOSCOPE_MODE"] = "demo"
        plaintext_case_v5()
        Path(argv[1]).write_text(m31b_owner(sys.stdin.readline().rstrip("\r\n")) or "", encoding="utf-8")
        print("fictional plaintext M3.1-B installation written")
    elif command == "inspect":
        print(json.dumps(inspect_database(), sort_keys=True))
    elif command == "interrupt":
        from app import encrypt_migrate

        def stop(step: str) -> None:
            if step == argv[1]:
                print(f"simulated interruption after step {step!r}")
                os._exit(3)   # like a crash: no cleanup, no finally blocks
        os.environ["ONCOSCOPE_MODE"] = "protected"
        encrypt_migrate._checkpoint = stop
        return encrypt_migrate.main(["--yes"])
    elif command == "sqlite3-read":
        import sqlite3
        from app import db
        try:
            rows = sqlite3.connect(db.database_path()).execute("SELECT label FROM patients").fetchall()
            print(json.dumps({"readable": True, "rows": len(rows)}))
        except sqlite3.DatabaseError as exc:
            print(json.dumps({"readable": False, "error": str(exc)}))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
