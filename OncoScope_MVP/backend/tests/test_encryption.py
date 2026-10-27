"""M3.1-C encrypted database: SQLCipher at rest, locked startup, the lossless migration tool and its recovery from
interruptions, encrypted backups and restores. Fictional data and throwaway keys only."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth, credentials, db, encrypt_migrate, vault
from app.backup import backup, verify_backup
from app.main import app
from app.restore import RestoreError, restore
from app.seed import seed_legacy_plaintext
from conftest import FIXTURES, new_source

sys.path.insert(0, str(FIXTURES.parent))
import legacy_installation as legacy  # noqa: E402  (fixtures/legacy_installation.py, shared with the browser demo)

PASSPHRASE = "fictional encryption passphrase"
NEW_PASSPHRASE = "another fictional encryption passphrase"
NOW = "2026-10-27T00:00:00Z"
APP_DIR = Path(__file__).resolve().parents[1]
# Values from the fictional seed case that must never appear in encrypted database bytes.
SENTINELS = [b"Synthetic Case A", b"Fictional serial laboratory results", b"Carboplatin + paclitaxel (fictional)",
             b"Baseline abdominal CT (synthetic)", b"Example soft-tissue lesion", b"Fictional initial oncology note"]


# --- helpers -------------------------------------------------------------------------------------------

@pytest.fixture
def data(tmp_path, monkeypatch) -> Path:
    """The database path of a fresh installation (still in demonstration mode until a test switches)."""
    path = tmp_path / "data" / "oncoscope.sqlite3"
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(path))
    for name in ("ONCOSCOPE_DOCUMENT_DIR", "ONCOSCOPE_VAULT_PATH", "ONCOSCOPE_SESSION_IDLE_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    return path


def protect(monkeypatch) -> None:
    monkeypatch.setenv("ONCOSCOPE_MODE", "protected")


def answers(passphrase=PASSPHRASE, recovery_key=None):
    given = {"passphrase": passphrase, "new_passphrase": passphrase, "recovery_key": recovery_key}
    return lambda kind: given[kind]


def h(reply: dict) -> dict:
    return {"Authorization": f"Bearer {reply['token']}"}


def unlock(c, passphrase=PASSPHRASE):
    return c.post("/api/auth/unlock", json={"passphrase": passphrase})


class Authed:
    """Sends the session header with every request, like the UI does."""
    def __init__(self, client, headers):
        self.client, self.headers = client, headers

    def __getattr__(self, method):
        return lambda url, **kw: getattr(self.client, method)(url, **{**kw, "headers": {**self.headers, **kw.get("headers", {})}})


def session_key() -> bytearray:
    [session] = auth._sessions.values()
    return session.db_key


@contextmanager
def encrypted_db(key):
    with db.keyed(key), db.connection() as conn:
        yield conn


def all_rows(conn) -> dict:
    return {t: [list(r) for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in encrypt_migrate._tables(conn)}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def m31b_installation(path: Path, *, with_vault: bool = True) -> str | None:
    """What M3.1-B (or, without a vault, M3.1-A) left behind on top of a plaintext v5 database. Returns the recovery key."""
    assert path == db.database_path()
    return legacy.m31b_owner(PASSPHRASE, with_vault=with_vault)


def rich_v5_case(path: Path, monkeypatch) -> dict:
    """A plaintext v5 database built through the API as M3.1-B's code wrote it, with the API views to compare later."""
    monkeypatch.setenv("ONCOSCOPE_MODE", "demo")
    case = legacy.plaintext_case_v5(lambda c, ids: api_views(c, ids["pid"], ids["lab"], ids["source"]))
    conn = db._connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    conn.close()
    return case


def api_views(c, pid: int, lab_id: int, source_id: int, headers: dict | None = None) -> dict:
    get = lambda url: c.get(url, headers=headers or {})
    report = "\n".join(line for line in get(f"/api/patients/{pid}/report.md").text.splitlines()
                       if not line.startswith("Generated:") and "chain" not in line.lower())
    export = get(f"/api/patients/{pid}/export.json").json()
    for volatile in ("generated_at", "audit_chain"):
        export.pop(volatile)
    return {path: get(path).json() for path in (
        "/api/patients", f"/api/patients/{pid}/timeline", f"/api/patients/{pid}/labs", f"/api/patients/{pid}/sources",
        f"/api/patients/{pid}/labs/series?test=CA-125", f"/api/patients/{pid}/evidence", f"/api/patients/{pid}/extractions",
        f"/api/patients/{pid}/audit", f"/api/records/labs/{lab_id}/history", f"/api/sources/{source_id}/document/verify",
        f"/api/sources/{source_id}/document/text")} | {"report": report, "export": export,
                                                         "document": hashlib.sha256(get(f"/api/sources/{source_id}/document").content).hexdigest()}


# 1-2. Encrypted at rest: ordinary SQLite cannot read it; no plaintext in the file, journals or temp files ----------

def test_a_new_protected_database_is_encrypted_and_unreadable_with_ordinary_sqlite(data, monkeypatch):
    protect(monkeypatch)
    with TestClient(app) as c:
        reply = c.post("/api/auth/setup", json={"display_name": "Fictional Owner", "passphrase": PASSPHRASE}).json()
        c.post("/api/patients", headers=h(reply), json={"label": "Fictional SENTINEL-AT-REST"})
        key = bytearray(session_key())
    assert db.file_kind(data) == "encrypted" and not data.read_bytes().startswith(db.SQLITE_HEADER)
    with pytest.raises(sqlite3.DatabaseError, match="file is not a database"):          # ordinary SQLite
        sqlite3.connect(data).execute("SELECT * FROM sqlite_master").fetchall()
    with pytest.raises(db.sqlite.DatabaseError, match="file is not a database"):        # SQLCipher without the key
        db.sqlite.connect(str(data)).execute("SELECT * FROM sqlite_master").fetchall()
    for wrong in (bytes(32), os.urandom(32)):
        with pytest.raises(db.DatabaseRefused if any(wrong) else db.DatabaseLocked):
            db.open_encrypted(data, wrong)
    conn = db.open_encrypted(data, key)
    assert conn.execute("SELECT label FROM patients").fetchone()[0] == "Fictional SENTINEL-AT-REST"
    assert conn.execute("PRAGMA cipher_memory_security").fetchone()[0] == "0"
    assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2                     # memory: no plaintext temp files
    conn.close()


def test_record_values_never_appear_in_database_bytes_journals_or_temporary_files(data, monkeypatch):
    protect(monkeypatch)
    with TestClient(app) as c:
        reply = c.post("/api/auth/setup", json={"display_name": "Fictional Owner", "passphrase": PASSPHRASE}).json()
        pid = c.post("/api/patients", headers=h(reply), json={"label": "Fictional SENTINEL-JOURNAL-CASE"}).json()["id"]
        key = bytearray(session_key())
        conn = db.open_encrypted(data, key)
        conn.execute("BEGIN")    # an open write transaction: the rollback journal exists now
        conn.execute("UPDATE patients SET label='Fictional SENTINEL-UPDATED' WHERE id=?", (pid,))
        conn.execute("CREATE TEMP TABLE scratch AS SELECT label || ' SENTINEL-TEMP' AS x FROM patients")
        conn.execute("SELECT x FROM scratch ORDER BY x").fetchall()
        journal = Path(f"{data}-journal")
        assert journal.exists()
        files = [p for p in data.parent.rglob("*") if p.is_file() and "documents" not in p.parts]
        assert journal in files and not Path(f"{data}-wal").exists()
        for path in files:
            raw = path.read_bytes()
            for sentinel in (b"SENTINEL-JOURNAL-CASE", b"SENTINEL-UPDATED", b"SENTINEL-TEMP"):
                assert sentinel not in raw, (path.name, sentinel)
            if path != db.vault_path():   # the vault header names the owner (pre-unlock metadata, see the docs)
                assert b"Fictional Owner" not in raw, path.name
        conn.rollback()
        conn.close()


# 3-6 and 12. Credentials, recovery, passphrase change, locking ------------------------------------------------

def test_credentials_recovery_and_passphrase_change_keep_the_same_database_key(data, monkeypatch):
    protect(monkeypatch)
    with TestClient(app) as c:
        first = c.post("/api/auth/setup", json={"display_name": "Fictional Owner", "passphrase": PASSPHRASE}).json()
        c.post("/api/vault/recovery-key/confirm", headers=h(first))
        c.post("/api/patients", headers=h(first), json={"label": "Fictional Recovery Case"})
        key, fingerprint = bytes(session_key()), c.get("/api/vault/status", headers=h(first)).json()["database"]["key_fingerprint"]
        db_hash = sha(data)

        # Wrong credentials: refused before SQLCipher sees anything, and nothing on disk changes.
        assert unlock(c, "fictional wrong passphrase").status_code == 401
        assert c.post("/api/auth/recover", json={"recovery_key": vault.create("x", PASSPHRASE, NOW, display_name="x")[1],
                                                 "new_passphrase": NEW_PASSPHRASE}).status_code == 401
        assert sha(data) == db_hash

        # Passphrase change: same DEK, same database key, same records.
        r = c.post("/api/vault/passphrase", headers=h(first), json={"current_passphrase": PASSPHRASE, "new_passphrase": NEW_PASSPHRASE})
        assert r.status_code == 200
        again = unlock(c, NEW_PASSPHRASE).json()
        assert bytes(session_key()) == key
        assert c.get("/api/patients", headers=h(again)).json()[0]["label"] == "Fictional Recovery Case"

        # Recovery key: the original records again, under a third passphrase.
        rec = c.post("/api/auth/recover", json={"recovery_key": first["recovery_key"], "new_passphrase": "third fictional passphrase"}).json()
        assert bytes(session_key()) == key
        assert c.get("/api/patients", headers=h(rec)).json()[0]["label"] == "Fictional Recovery Case"
        assert c.get("/api/vault/status", headers=h(rec)).json()["database"]["key_fingerprint"] == fingerprint
        # Failed attempts made while locked are recorded with the next successful unlock.
        with encrypted_db(key) as conn:
            details = [json.loads(r[0]) for r in conn.execute("SELECT detail_json FROM audit_log WHERE action='unlock' ORDER BY id")]
        assert details[-2]["failed_attempts_before_unlock"] == 2 and details[-1]["failed_attempts_before_unlock"] == 0

    # Restart: still the same database and key.
    with TestClient(app) as restarted:
        assert restarted.get("/api/patients").status_code == 401
        reply = unlock(restarted, "third fictional passphrase").json()
        assert bytes(session_key()) == key and restarted.get("/api/patients", headers=h(reply)).status_code == 200


def test_locking_and_expiry_revoke_database_access(data, monkeypatch):
    protect(monkeypatch)
    with TestClient(app) as c:
        reply = c.post("/api/auth/setup", json={"display_name": "Fictional Owner", "passphrase": PASSPHRASE}).json()
        held = session_key()
        assert c.get("/api/patients", headers=h(reply)).status_code == 200
        c.post("/api/auth/lock", headers=h(reply))
        assert held == bytearray(32)                                         # the session's key was wiped in place
        assert c.get("/api/patients", headers=h(reply)).status_code == 401
        with pytest.raises(db.DatabaseLocked), encrypted_db(held):           # a kept reference is useless now
            pass
        with pytest.raises(db.DatabaseLocked), db.connection():
            pass
        second = unlock(c).json()
        held = session_key()
        monkeypatch.setenv("ONCOSCOPE_SESSION_IDLE_SECONDS", "0")
        import time
        time.sleep(0.01)
        assert auth.sweep() == 1 and held == bytearray(32)
        assert c.get("/api/patients", headers=h(second)).status_code == 401


# 10. Missing or corrupted vaults and databases never lead to a new database -----------------------------------

def test_missing_or_damaged_files_are_refused_and_never_recreated(data, monkeypatch):
    protect(monkeypatch)
    with TestClient(app) as c:
        c.post("/api/auth/setup", json={"display_name": "Fictional Owner", "passphrase": PASSPHRASE})
        vault_bytes, db_bytes = db.vault_path().read_bytes(), data.read_bytes()

        def refused(expected: str):
            state = c.get("/api/auth/status").json()
            assert state["storage"] == "refused" and expected in state["storage_message"], state
            assert unlock(c).status_code == 409
            assert c.post("/api/auth/setup", json={"display_name": "Fictional Intruder", "passphrase": PASSPHRASE}).status_code == 409

        db.vault_path().unlink()
        refused("key vault file is missing")
        assert not db.vault_path().exists() and data.read_bytes() == db_bytes
        db.vault_path().write_bytes(vault_bytes[: len(vault_bytes) // 2])
        refused("damaged")
        db.vault_path().write_bytes(vault_bytes)

        data.unlink()
        refused("missing or empty")
        assert not data.exists()                                             # no new database was created
        data.write_bytes(b"")
        refused("missing or empty")
        assert data.read_bytes() == b""

        plain = sqlite3.connect(data)                                        # a plaintext file swapped in
        plain.execute("CREATE TABLE patients(label TEXT)")
        plain.close()
        refused("not encrypted")
        data.write_bytes(db_bytes[:4096] + b"\x00" * (len(db_bytes) - 4096))   # damaged pages after the first
        r = unlock(c)
        assert r.status_code == 409 and "damaged" in r.json()["detail"]
        data.write_bytes(db_bytes)
        assert unlock(c).status_code == 200


def test_an_unavailable_sqlcipher_fails_clearly(monkeypatch):
    class NoCipher:
        @staticmethod
        def connect(_):
            return sqlite3.connect(":memory:")        # plain SQLite: PRAGMA cipher_version returns nothing
    monkeypatch.setattr(db, "sqlite", NoCipher)
    with pytest.raises(RuntimeError, match="no SQLCipher support"):
        db._cipher_version()


# 7-8. Lossless migration of existing plaintext databases ----------------------------------------------------------

def test_an_m31b_installation_migrates_without_loss(data, monkeypatch):
    case = rich_v5_case(data, monkeypatch)
    recovery_key = m31b_installation(data)
    plain = db._connect(data)
    before = all_rows(plain)
    plain.close()
    original_bytes = data.read_bytes()
    protect(monkeypatch)
    with TestClient(app) as c:
        assert c.get("/api/auth/status").json()["storage"] == "migration_required"
        assert unlock(c).status_code == 409

    result = encrypt_migrate.run(answers())
    assert result["status"] == "encrypted" and result["plaintext_values_checked"] > 50
    assert db.file_kind(data) == "encrypted" and not encrypt_migrate.staging_path().exists()
    assert not db.migration_state_path().exists()
    doc = vault.read(db.vault_path())
    assert doc["version"] == 2 and doc["database"]["state"] == "encrypted" and doc["database"]["migrated_from_plaintext_at"]
    for sentinel in (b"SENTINEL-CASE", b"SENTINEL-SOURCE", b"SENTINEL-EVENT", b"Fictional typo", b"CA-125          310",
                     *SENTINELS):
        assert sentinel not in data.read_bytes(), sentinel

    # The verified plaintext backup is the old database, byte-for-byte in content (and is flagged as unencrypted).
    backup_dir = Path(result["plaintext_backup"])
    assert backup_dir.name.startswith("oncoscope-pre-encryption-") and verify_backup(backup_dir)["ok"]
    assert db.file_kind(backup_dir / "oncoscope.sqlite3") == "plaintext"

    with TestClient(app) as c:
        reply = unlock(c).json()
        key = bytes(session_key())
        after_views = api_views(c, case["pid"], case["lab"], case["source"], h(reply))
        with encrypted_db(key) as conn:
            after = all_rows(conn)
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
            versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert after_views == case["views"]                                     # the case looks and behaves identically
    assert versions == [1, 2, 3, 4, 5, 6]
    # Every row that existed is unchanged (values, provenance, timestamps, revisions, verification, hashes). The owner
    # row lost only the retired verifier column (migration 6); the audit log only gained entries at its end.
    for table, rows in before.items():
        if table == "local_owner":
            assert [r[:3] + r[4:] for r in rows] == after[table]
        elif table == "audit_log":
            assert after[table][: len(rows)] == rows
        elif table == "schema_migrations":
            assert after[table][:5] == rows
        else:
            assert after[table] == rows, table
    added = [r[3] for r in after["audit_log"][len(before["audit_log"]):]]
    assert added == ["vault_upgraded", "database_encryption_started", "database_encrypted", "unlock"]
    assert data.read_bytes() != original_bytes

    # The recovery key issued under M3.1-B (v1 vault) still opens the migrated installation.
    with TestClient(app) as c:
        rec = c.post("/api/auth/recover", json={"recovery_key": recovery_key, "new_passphrase": NEW_PASSPHRASE})
        assert rec.status_code == 200, rec.text
        assert c.get("/api/patients", headers=h(rec.json())).json()[0]["label"].endswith("SENTINEL-CASE")


def test_an_m31a_installation_gets_its_vault_during_the_migration(data, monkeypatch):
    seed_legacy_plaintext(data, schema_version=4)
    m31b_installation(data, with_vault=False)
    protect(monkeypatch)
    with pytest.raises(encrypt_migrate.MigrationError, match="Incorrect passphrase"):
        encrypt_migrate.run(answers("fictional wrong passphrase"))
    assert not db.vault_path().exists() and not db.migration_state_path().exists() and db.file_kind(data) == "plaintext"
    shown = []
    result = encrypt_migrate.run(answers(), on_recovery_key=lambda opened, key: shown.append(key))
    assert result["status"] == "encrypted" and len(shown) == 1
    with TestClient(app) as c:
        reply = unlock(c).json()
        assert reply["recovery_key"] and reply["recovery_key"] != shown[0]     # never confirmed: a fresh one is shown
        assert c.get("/api/patients", headers=h(reply)).json()[0]["label"] == "Synthetic Case A"


def test_an_interrupted_initialisation_vault_is_adopted_not_replaced(data, monkeypatch):
    seed_legacy_plaintext(data, schema_version=5)
    m31b_installation(data, with_vault=False)
    conn = db._connect(data)
    uid = conn.execute("SELECT uid FROM local_owner").fetchone()[0]
    conn.close()
    left, _ = vault.create(uid, PASSPHRASE, NOW, display_name="Fictional Owner", version=1)   # written, never bound
    vault.write(db.vault_path(), left.doc, left.dek, replace_existing=False)
    protect(monkeypatch)
    encrypt_migrate.run(answers())
    assert vault.read(db.vault_path())["key_id"] == left.doc["key_id"]
    with TestClient(app) as c:
        assert unlock(c).status_code == 200 and bytes(session_key()) == vault.derive(left.dek, "database")


def test_the_migration_refuses_unsafe_starting_points(data, monkeypatch):
    seed_legacy_plaintext(data)
    recovery_key = m31b_installation(data)
    protect(monkeypatch)
    vault_hash = sha(db.vault_path())

    def refused(match, ask=answers(), **kw):
        untouched = sha(data)
        with pytest.raises(encrypt_migrate.MigrationError, match=match):
            encrypt_migrate.run(ask, **kw)
        assert sha(data) == untouched and db.file_kind(data) == "plaintext"
        assert not db.migration_state_path().exists() and not encrypt_migrate.staging_path().exists()

    refused("Incorrect passphrase", answers("fictional wrong passphrase"))
    refused("Incorrect recovery key", answers(recovery_key=vault.create("x", PASSPHRASE, NOW, display_name="x")[1]),
            use_recovery_key=True)
    refused("not a valid recovery key", answers(recovery_key="ABCD-EFGH"), use_recovery_key=True)
    assert sha(db.vault_path()) == vault_hash

    conn = db._connect(data)
    conn.execute("UPDATE patients SET is_synthetic=0")
    conn.commit()
    conn.close()
    refused("not marked synthetic")
    conn = db._connect(data)
    conn.execute("UPDATE patients SET is_synthetic=1")
    conn.commit()
    conn.close()

    vault_bytes = db.vault_path().read_bytes()
    db.vault_path().unlink()
    refused("key vault file is missing")
    assert not db.vault_path().exists()                                      # never a new key for this installation
    db.vault_path().write_bytes(vault_bytes)

    conn = db._connect(data)
    conn.execute("DROP TRIGGER audit_log_no_update")
    conn.execute("UPDATE audit_log SET reason='FICTIONAL tampered' WHERE id=2")
    conn.commit()
    conn.close()
    with pytest.raises(encrypt_migrate.MigrationError, match="audit chain invalid"):
        encrypt_migrate.run(answers())
    assert db.file_kind(data) == "plaintext" and not encrypt_migrate.staging_path().exists()

    monkeypatch.setenv("ONCOSCOPE_MODE", "demo")
    with pytest.raises(encrypt_migrate.MigrationError, match="protected mode"):
        encrypt_migrate.run(answers())


# 9. Interrupted migrations ----------------------------------------------------------------------------------------

class Interrupted(Exception):
    pass


def interrupt(monkeypatch, step: str):
    def hook(name):
        if name == step:
            raise Interrupted(step)
    monkeypatch.setattr(encrypt_migrate, "_checkpoint", hook)


def interrupt_swap(monkeypatch):
    """Power loss during the final file replacement: os.replace of the database itself fails."""
    real = os.replace

    def replace(src, dst, *a, **kw):
        if Path(dst) == db.database_path():
            raise OSError("simulated power loss during the final replacement")
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(encrypt_migrate.os, "replace", replace)
    return real


def plaintext_installation(data, monkeypatch) -> dict:
    seed_legacy_plaintext(data)
    m31b_installation(data)
    conn = db._connect(data)
    rows = all_rows(conn)
    conn.close()
    protect(monkeypatch)
    return rows


STEPS = ["started", "backed_up", "exported", "verified", "vault_marked", "swap", "replaced", "audited"]


@pytest.mark.parametrize("step", STEPS)
def test_an_interrupted_migration_is_completed_by_running_it_again(data, monkeypatch, step):
    before = plaintext_installation(data, monkeypatch)
    original = encrypt_migrate._checkpoint
    real_replace = None
    if step == "swap":
        real_replace = interrupt_swap(monkeypatch)
    else:
        interrupt(monkeypatch, step)
    with pytest.raises((Interrupted, OSError)):
        encrypt_migrate.run(answers())
    monkeypatch.setattr(encrypt_migrate, "_checkpoint", original)
    if real_replace:
        monkeypatch.setattr(encrypt_migrate.os, "replace", real_replace)

    # In between, the server refuses to unlock rather than guess.
    assert db.migration_state_path().exists()
    with TestClient(app) as c:
        state = c.get("/api/auth/status").json()
        assert state["storage"] == "migration_interrupted" and unlock(c).status_code == 409
    expected_kind = "encrypted" if step in ("replaced", "audited") else "plaintext"
    assert db.file_kind(data) == expected_kind
    if step == "exported":                                    # a torn copy from a crash mid-export is discarded and redone
        staging = encrypt_migrate.staging_path()
        staging.write_bytes(staging.read_bytes()[: staging.stat().st_size // 2])

    result = encrypt_migrate.run(answers())
    assert result["status"] == "encrypted"
    assert db.file_kind(data) == "encrypted" and not db.migration_state_path().exists()
    assert not encrypt_migrate.staging_path().exists()
    assert len(list((data.parent / "backups").iterdir())) == 1          # the verified backup was reused, not repeated
    with TestClient(app) as c:
        unlock(c)
        with encrypted_db(session_key()) as conn:
            after = all_rows(conn)
            assert db.verify_audit_chain(conn)["valid"]
    for table, rows in before.items():
        if table not in ("audit_log", "local_owner", "schema_migrations"):
            assert after[table] == rows, table                           # record hashes unchanged
    assert after["audit_log"][: len(before["audit_log"])] == before["audit_log"]
    actions = [r[3] for r in after["audit_log"]]
    assert actions.count("database_encrypted") == 1 and actions.count("database_encryption_started") == 1


@pytest.mark.parametrize("step", ["started", "backed_up", "exported", "verified", "vault_marked", "swap"])
def test_an_interrupted_migration_can_be_rolled_back_before_activation(data, monkeypatch, step):
    before = plaintext_installation(data, monkeypatch)
    original = encrypt_migrate._checkpoint
    if step == "swap":
        real_replace = interrupt_swap(monkeypatch)
    else:
        interrupt(monkeypatch, step)
    with pytest.raises((Interrupted, OSError)):
        encrypt_migrate.run(answers())
    monkeypatch.setattr(encrypt_migrate, "_checkpoint", original)
    if step == "swap":
        monkeypatch.setattr(encrypt_migrate.os, "replace", real_replace)
    with pytest.raises(encrypt_migrate.MigrationError, match="Incorrect passphrase"):
        encrypt_migrate.rollback(answers("fictional wrong passphrase"))
    result = encrypt_migrate.rollback(answers())
    assert result["status"] == "rolled_back"
    assert db.file_kind(data) == "plaintext"
    conn = db._connect(data)
    after = all_rows(conn)
    conn.close()
    for table, rows in before.items():        # records untouched; the audit log only gained the tool's own entries
        assert (after[table][: len(rows)] if table == "audit_log" else after[table]) == rows, table
    assert vault.database_state(vault.read(db.vault_path())) == "plaintext"
    assert not db.migration_state_path().exists() and not encrypt_migrate.staging_path().exists()
    with TestClient(app) as c:
        assert c.get("/api/auth/status").json()["storage"] == "migration_required"
    assert encrypt_migrate.run(answers())["status"] == "encrypted"          # and a later run starts cleanly


def test_a_missing_database_mid_swap_is_completed_from_the_verified_copy(data, monkeypatch):
    before = plaintext_installation(data, monkeypatch)
    original = encrypt_migrate._checkpoint
    interrupt(monkeypatch, "vault_marked")
    with pytest.raises(Interrupted):
        encrypt_migrate.run(answers())
    monkeypatch.setattr(encrypt_migrate, "_checkpoint", original)
    data.unlink()            # a file system that lost the original during the replacement
    assert encrypt_migrate.run(answers())["status"] == "encrypted"
    with TestClient(app) as c:
        unlock(c)
        with encrypted_db(session_key()) as conn:
            assert all_rows(conn)["labs"] == before["labs"]


def test_rollback_is_refused_after_activation_and_a_damaged_record_stops_the_tool(data, monkeypatch):
    plaintext_installation(data, monkeypatch)
    original = encrypt_migrate._checkpoint
    interrupt(monkeypatch, "replaced")
    with pytest.raises(Interrupted):
        encrypt_migrate.run(answers())
    monkeypatch.setattr(encrypt_migrate, "_checkpoint", original)
    with pytest.raises(encrypt_migrate.MigrationError, match="cannot be rolled back"):
        encrypt_migrate.rollback(answers())
    record = db.migration_state_path().read_bytes()
    db.migration_state_path().write_bytes(b"{not json")
    with pytest.raises(encrypt_migrate.MigrationError, match="damaged"):
        encrypt_migrate.run(answers())
    db.migration_state_path().write_bytes(record)
    assert encrypt_migrate.run(answers())["status"] == "encrypted"
    assert encrypt_migrate.run(answers())["status"] == "already_encrypted"


def test_the_migration_command_line_with_scripted_input(data, monkeypatch):
    seed_legacy_plaintext(data, schema_version=3)
    env = {**os.environ, "ONCOSCOPE_MODE": "protected", "ONCOSCOPE_DB_PATH": str(data)}
    env.pop("ONCOSCOPE_DOCUMENT_DIR", None)
    run = lambda *args, stdin: subprocess.run([sys.executable, "-m", *args], cwd=APP_DIR, env=env, input=stdin,
                                              capture_output=True, text=True, timeout=120)
    done = run("app.encrypt_migrate", "--display-name", "Fictional CLI Owner", stdin=PASSPHRASE + "\n")
    assert done.returncode == 0, done.stdout + done.stderr
    assert '"status": "encrypted"' in done.stdout and "NOT encrypted" in done.stdout
    assert PASSPHRASE not in done.stdout + done.stderr and "RECOVERY KEY" not in done.stdout   # no terminal: never printed
    wrong = run("app.backup", stdin="fictional wrong passphrase\n")
    assert wrong.returncode == 1 and "incorrect passphrase" in wrong.stderr
    made = run("app.backup", str(data.parent / "cli-backups"), stdin=PASSPHRASE + "\n")
    assert made.returncode == 0, made.stdout + made.stderr
    [backup_dir] = (data.parent / "cli-backups").iterdir()
    assert db.file_kind(backup_dir / "oncoscope.sqlite3") == "encrypted"
    restored = run("app.restore", str(backup_dir), "--target", str(data.parent.parent / "clean" / "oncoscope.sqlite3"),
                   stdin=PASSPHRASE + "\n")
    assert restored.returncode == 0 and '"encrypted": true' in restored.stdout, restored.stdout


# 11. Encrypted backups and restores -----------------------------------------------------------------------------

def test_encrypted_backups_stay_encrypted_and_restore_with_either_credential(data, tmp_path, monkeypatch):
    protect(monkeypatch)
    with TestClient(app) as c:
        reply = c.post("/api/auth/setup", json={"display_name": "Fictional Owner", "passphrase": PASSPHRASE}).json()
        c.post("/api/vault/recovery-key/confirm", headers=h(reply))
        pid = c.post("/api/patients", headers=h(reply), json={"label": "Fictional SENTINEL-BACKUP"}).json()["id"]
        new_source(Authed(c, h(reply)), pid, "Fictional backup lab", document="lab_2026-08-07_imaginary_valley.txt")
        key = bytearray(session_key())
    with pytest.raises(SystemExit, match="needs the owner's passphrase"):
        backup(tmp_path / "backups")
    made = backup(tmp_path / "backups", key=key)
    copy = made / "oncoscope.sqlite3"
    assert db.file_kind(copy) == "encrypted" and b"SENTINEL-BACKUP" not in copy.read_bytes()
    manifest = json.loads((made / "manifest.json").read_text())
    assert manifest["database_encryption"]["encrypted"] is True and b"SENTINEL-BACKUP" not in (made / "manifest.json").read_bytes()
    assert verify_backup(made, key)["ok"]
    assert "needs the passphrase" in verify_backup(made)["problems"][0]
    assert not verify_backup(made, os.urandom(32))["ok"]

    # Clean folders, once with the passphrase and once with the recovery key alone (both derive the key from the
    # backup's own vault); then the server there unlocks and shows the case.
    for name, kw in (("by-passphrase", {"secret": PASSPHRASE}),
                     ("by-recovery-key", {"secret": reply["recovery_key"], "use_recovery_key": True})):
        derived = credentials.database_key_from_vault(made / "oncoscope-vault.json", **kw)
        target = tmp_path / name / "oncoscope.sqlite3"
        result = restore(made, target, key=derived)
        assert result["consistent"] and result["encrypted"], result["differences"]
        assert b"SENTINEL-BACKUP" not in target.read_bytes()
        monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(target))
        with TestClient(app) as moved:
            r = unlock(moved) if name == "by-passphrase" else moved.post(
                "/api/auth/recover", json={"recovery_key": reply["recovery_key"], "new_passphrase": NEW_PASSPHRASE})
            assert r.status_code == 200, r.text
            assert moved.get("/api/patients", headers=h(r.json())).json()[0]["label"] == "Fictional SENTINEL-BACKUP"
            assert moved.get(f"/api/sources/1/document/verify", headers=h(r.json())).json()["valid"] is True
    with pytest.raises(credentials.CredentialError, match="incorrect passphrase"):
        credentials.database_key_from_vault(made / "oncoscope-vault.json", secret="fictional wrong passphrase")
    with pytest.raises(RestoreError, match="nothing was restored"):
        restore(made, tmp_path / "wrong-key" / "oncoscope.sqlite3", key=os.urandom(32))
    assert not (tmp_path / "wrong-key" / "oncoscope.sqlite3").exists()


@pytest.mark.parametrize("damage", ["flip-page", "truncate", "missing-vault", "foreign-vault", "plaintext-swap", "no-key"])
def test_damaged_or_partial_encrypted_backups_fail_safely(data, tmp_path, monkeypatch, damage):
    protect(monkeypatch)
    with TestClient(app) as c:
        reply = c.post("/api/auth/setup", json={"display_name": "Fictional Owner", "passphrase": PASSPHRASE}).json()
        c.post("/api/patients", headers=h(reply), json={"label": "Fictional damaged backup"})
        key = bytearray(session_key())
    made = backup(tmp_path / "backups", key=key)
    copy, vault_copy = made / "oncoscope.sqlite3", made / "oncoscope-vault.json"
    if damage == "flip-page":
        raw = bytearray(copy.read_bytes())
        raw[5000] ^= 0x01
        copy.write_bytes(bytes(raw))
    elif damage == "truncate":
        copy.write_bytes(copy.read_bytes()[: len(copy.read_bytes()) // 2])
    elif damage == "missing-vault":
        vault_copy.unlink()
    elif damage == "foreign-vault":
        other, _ = vault.create("someone-else", PASSPHRASE, NOW, display_name="Fictional Other", database_state="encrypted")
        vault.write(vault_copy, other.doc, other.dek, replace_existing=True)
    elif damage == "plaintext-swap":
        copy.unlink()
        sqlite3.connect(copy).execute("CREATE TABLE patients(id INTEGER)").connection.close()
    check = verify_backup(made, None if damage == "no-key" else key)
    assert not check["ok"], damage
    target = tmp_path / "target" / "oncoscope.sqlite3"
    with pytest.raises(RestoreError, match="nothing was restored"):
        restore(made, target, key=None if damage == "no-key" else key)
    assert not target.exists() and not (target.parent / "oncoscope-vault.json").exists()
