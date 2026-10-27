"""M3.1-B key vault: DEK recovery by passphrase and recovery key, passphrase change, tampering, interrupted writes,
restarts, backups, and isolation from demonstration mode. Fictional data and throwaway keys only."""
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, db, vault
from app.backup import backup, verify_backup
from app.main import app
from app.restore import RestoreError, restore

PASSPHRASE = "fictional vault passphrase"
NEW_PASSPHRASE = "another fictional passphrase"
NOW = "2026-10-27T00:00:00Z"
APP_DIR = Path(__file__).resolve().parents[1]


# --- helpers -------------------------------------------------------------------------------------------

@pytest.fixture
def protected(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOSCOPE_MODE", "protected")
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "data" / "oncoscope.sqlite3"))
    monkeypatch.delenv("ONCOSCOPE_DOCUMENT_DIR", raising=False)
    monkeypatch.delenv("ONCOSCOPE_VAULT_PATH", raising=False)
    with TestClient(app) as c:
        yield c


def setup(c) -> dict:
    r = c.post("/api/auth/setup", json={"display_name": "Fictional Owner", "passphrase": PASSPHRASE})
    assert r.status_code == 201, r.text
    return r.json()


def h(reply: dict) -> dict:
    return {"Authorization": f"Bearer {reply['token']}"}


def unlock(c, passphrase=PASSPHRASE):
    return c.post("/api/auth/unlock", json={"passphrase": passphrase})


def recover(c, key, new=NEW_PASSPHRASE):
    return c.post("/api/auth/recover", json={"recovery_key": key, "new_passphrase": new})


def confirm(c, reply):
    assert c.post("/api/vault/recovery-key/confirm", headers=h(reply)).status_code == 200


def status(c, reply) -> dict:
    r = c.get("/api/vault/status", headers=h(reply))
    assert r.status_code == 200, r.text
    return r.json()


def live_dek() -> bytes:
    [session] = auth._sessions.values()
    return bytes(session.keys.dek)


def vault_file() -> Path:
    return db.vault_path()


def owner_row() -> dict:
    with db.connection() as conn:
        return dict(conn.execute("SELECT * FROM local_owner").fetchone())


def actions() -> list[str]:
    with db.connection() as conn:
        return [r[0] for r in conn.execute("SELECT action FROM audit_log ORDER BY id")]


def flip(data: bytes, at: int) -> bytes:
    return data[:at] + bytes([data[at] ^ 0x01]) + data[at + 1:]


# 1-5. The vault itself -------------------------------------------------------------------------------------

def test_passphrase_and_recovery_key_open_the_same_dek():
    opened, recovery_key = vault.create("owner-uid", PASSPHRASE, NOW)
    doc = vault.parse(json.dumps(opened.doc).encode())
    assert vault.open_with_passphrase(doc, PASSPHRASE).dek == opened.dek
    assert vault.open_with_recovery_key(doc, recovery_key).dek == opened.dek
    assert len(opened.dek) == 32 and doc["key_id"] == vault.key_id(opened.dek)
    assert len(recovery_key) == 69 and recovery_key.count("-") == 13       # 14 groups of 4
    # Forgiving input: lower case, spaces instead of hyphens, 0/1/8 typed for O/I/B.
    sloppy = recovery_key.lower().replace("-", " ").replace("o", "0").replace("i", "1").replace("b", "8")
    assert vault.open_with_recovery_key(doc, sloppy).dek == opened.dek


def test_wrong_credentials_fail_cleanly():
    opened, recovery_key = vault.create("owner-uid", PASSPHRASE, NOW)
    other, other_key = vault.create("owner-uid", PASSPHRASE, NOW)
    with pytest.raises(vault.WrongCredential):
        vault.open_with_passphrase(opened.doc, "fictional wrong passphrase")
    with pytest.raises(vault.WrongCredential, match="recovery"):
        vault.open_with_recovery_key(opened.doc, other_key)                # well-formed, but another vault's key
    typo = recovery_key[:-1] + ("A" if recovery_key[-1] != "A" else "B")
    with pytest.raises(vault.WrongCredential, match="checksum"):
        vault.open_with_recovery_key(opened.doc, typo)
    with pytest.raises(vault.WrongCredential, match="format"):
        vault.open_with_recovery_key(opened.doc, "not a key")
    with pytest.raises(vault.WrongCredential):                             # a wrap moved from another vault
        vault.open_with_passphrase({**opened.doc, "wraps": other.doc["wraps"]}, PASSPHRASE)


def test_passphrase_change_keeps_the_dek_and_the_recovery_key():
    opened, recovery_key = vault.create("owner-uid", PASSPHRASE, NOW)
    changed = vault.with_passphrase(opened, NEW_PASSPHRASE, NOW)
    with pytest.raises(vault.WrongCredential):
        vault.open_with_passphrase(changed, PASSPHRASE)
    assert vault.open_with_passphrase(changed, NEW_PASSPHRASE).dek == opened.dek
    assert vault.open_with_recovery_key(changed, recovery_key).dek == opened.dek
    assert changed["wraps"]["passphrase"]["salt"] != opened.doc["wraps"]["passphrase"]["salt"]


def test_derived_keys_are_reproducible_separate_and_never_the_dek():
    opened, recovery_key = vault.create("owner-uid", PASSPHRASE, NOW)
    keys = {p: vault.derive(opened.dek, p) for p in vault.PURPOSES}
    assert len(set(keys.values())) == len(vault.PURPOSES) and bytes(opened.dek) not in keys.values()
    assert all(len(k) == 32 for k in keys.values())
    recovered = vault.open_with_recovery_key(vault.with_passphrase(opened, NEW_PASSPHRASE, NOW), recovery_key)
    assert {p: vault.derive(recovered.dek, p) for p in vault.PURPOSES} == keys
    other, _ = vault.create("owner-uid", PASSPHRASE, NOW)
    assert vault.derive(other.dek, "database") != keys["database"]
    with pytest.raises(ValueError):
        vault.derive(opened.dek, "something-else")


# 6. Corrupted, truncated, unsupported and tampered vaults -------------------------------------------------

def _sealed_bytes():
    opened, recovery_key = vault.create("owner-uid", PASSPHRASE, NOW)
    return json.dumps(opened.doc, indent=2, sort_keys=True).encode(), opened, recovery_key


@pytest.mark.parametrize("damage", ["truncate-half", "truncate-one", "empty", "not-json", "flip-ciphertext", "flip-salt",
                                    "wrong-format", "missing-wrap", "bad-argon2", "bool-argon2"])
def test_damaged_vaults_are_refused_as_damaged(damage):
    raw, _, _ = _sealed_bytes()
    doc = json.loads(raw)
    if damage == "truncate-half":
        bad = raw[: len(raw) // 2]
    elif damage == "truncate-one":
        bad = raw[:-3]
    elif damage == "empty":
        bad = b""
    elif damage == "not-json":
        bad = b"\x00\xff" + raw
    elif damage == "flip-ciphertext":
        ct = doc["wraps"]["passphrase"]["ciphertext"]
        doc["wraps"]["passphrase"]["ciphertext"] = ("B" if ct[0] != "B" else "C") + ct[1:]
        bad = json.dumps(doc).encode()
    elif damage == "flip-salt":
        doc["wraps"]["recovery"]["salt"] = base64.b64encode(b"\x00" * 16).decode()
        bad = json.dumps(doc).encode()
    elif damage == "wrong-format":
        bad = json.dumps({**doc, "format": "something-else"}).encode()
    elif damage == "missing-wrap":
        del doc["wraps"]["recovery"]
        bad = json.dumps(doc).encode()
    elif damage == "bad-argon2":
        doc["wraps"]["passphrase"]["memory_cost"] = 8
        bad = json.dumps(doc).encode()
    else:
        doc["wraps"]["passphrase"]["time_cost"] = True
        bad = json.dumps(doc).encode()
    with pytest.raises(vault.VaultError) as err:
        vault.parse(bad)
    assert err.value.code == "damaged"


def test_unsupported_versions_are_refused():
    raw, _, _ = _sealed_bytes()
    with pytest.raises(vault.VaultError) as err:
        vault.parse(json.dumps({**json.loads(raw), "version": 2}).encode())
    assert err.value.code == "unsupported"


def _reseal_checksum(doc: dict) -> dict:
    import hashlib
    body = {k: v for k, v in doc.items() if k != "checksum"}
    return {**body, "checksum": hashlib.sha256(vault._canonical(body)).hexdigest()}


def test_deliberate_edits_that_fix_the_checksum_are_still_detected():
    raw, opened, recovery_key = _sealed_bytes()
    doc = json.loads(raw)
    # Header fields outside the associated data: caught by the DEK-keyed integrity code.
    for field, value in (("owner_uid", "someone-else"), ("recovery_confirmed", True), ("created_at", "2020-01-01T00:00:00Z")):
        edited = vault.parse(json.dumps(_reseal_checksum({**doc, field: value})).encode())
        with pytest.raises(vault.VaultError) as err:
            vault.open_with_passphrase(edited, PASSPHRASE)
        assert err.value.code == "tampered", field
    # Fields bound into the wraps (ids, KDF parameters, ciphertext): the unwrap itself fails.
    for change in ({"vault_id": "other"}, {"key_id": "0" * 32}):
        edited = vault.parse(json.dumps(_reseal_checksum({**doc, **change})).encode())
        with pytest.raises(vault.WrongCredential):
            vault.open_with_passphrase(edited, PASSPHRASE)
        with pytest.raises(vault.WrongCredential):
            vault.open_with_recovery_key(edited, recovery_key)
    slower = json.loads(raw)
    slower["wraps"]["passphrase"]["time_cost"] = 4
    with pytest.raises(vault.WrongCredential):
        vault.open_with_passphrase(vault.parse(json.dumps(_reseal_checksum(slower)).encode()), PASSPHRASE)


# 9. Atomic, verified writes --------------------------------------------------------------------------------

def test_interrupted_write_keeps_the_previous_vault(tmp_path, monkeypatch):
    path = tmp_path / "vault.json"
    opened, _ = vault.create("owner-uid", PASSPHRASE, NOW)
    vault.write(path, opened.doc, opened.dek, replace_existing=False)
    before = path.read_bytes()

    def crash(*_):
        raise OSError("simulated power loss during rename")
    real_replace = os.replace
    monkeypatch.setattr(vault.os, "replace", crash)
    with pytest.raises(OSError):
        vault.write(path, vault.with_passphrase(opened, NEW_PASSPHRASE, NOW), opened.dek, replace_existing=True)
    monkeypatch.setattr(vault.os, "replace", real_replace)
    assert path.read_bytes() == before and vault.stale_temp_files(path) == []
    assert vault.open_with_passphrase(vault.read(path), PASSPHRASE).dek == opened.dek


def test_a_write_that_does_not_verify_is_rolled_back(tmp_path, monkeypatch):
    path = tmp_path / "vault.json"
    opened, _ = vault.create("owner-uid", PASSPHRASE, NOW)
    vault.write(path, opened.doc, opened.dek, replace_existing=False)
    before = path.read_bytes()
    other, _ = vault.create("owner-uid", PASSPHRASE, NOW)
    with pytest.raises(vault.VaultError) as err:      # a document sealed for another DEK fails the read-back check
        vault.write(path, other.doc, opened.dek, replace_existing=True)
    assert err.value.code == "write_failed" and path.read_bytes() == before


def test_existing_vaults_are_never_replaced_by_default(tmp_path):
    path = tmp_path / "vault.json"
    opened, _ = vault.create("owner-uid", PASSPHRASE, NOW)
    vault.write(path, opened.doc, opened.dek, replace_existing=False)
    other, _ = vault.create("owner-uid", PASSPHRASE, NOW)
    with pytest.raises(vault.VaultError) as err:
        vault.write(path, other.doc, other.dek, replace_existing=False)
    assert err.value.code == "exists" and vault.read(path)["vault_id"] == opened.doc["vault_id"]


def test_lock_wipes_the_dek_from_memory():
    opened, _ = vault.create("owner-uid", PASSPHRASE, NOW)
    keep = opened.dek
    opened.wipe()
    assert keep == bytearray(32)


# API: setup, unlock, recovery, passphrase change ------------------------------------------------------------

def test_setup_creates_a_vault_and_shows_the_recovery_key_until_confirmed(protected):
    first = setup(protected)
    key1 = first["recovery_key"]
    assert key1 and first["vault"]["recovery_confirmed"] is False
    assert vault_file().exists() and owner_row()["vault_id"] == vault.read(vault_file())["vault_id"]
    info = status(protected, first)
    assert info["key_id"] == first["vault"]["key_id"] and info["encrypted_at_rest"] is False
    assert set(info["derived_key_fingerprints"]) == set(vault.PURPOSES)
    # Not confirmed yet: the next unlock issues a fresh key (the unseen one stops working) …
    second = unlock(protected).json()
    key2 = second["recovery_key"]
    assert key2 and key2 != key1
    assert recover(protected, key1).status_code == 401
    confirm(protected, second)
    # … once confirmed, no key is shown again.
    third = unlock(protected).json()
    assert third["recovery_key"] is None and third["vault"]["recovery_confirmed"] is True
    assert {"owner_setup", "vault_initialized", "recovery_key_issued", "recovery_key_confirmed"} <= set(actions())


def test_recovery_key_restores_the_same_dek_and_sets_a_new_passphrase(protected):
    reply = setup(protected)
    confirm(protected, reply)
    dek, fingerprints = live_dek(), status(protected, reply)["derived_key_fingerprints"]
    rec = recover(protected, reply["recovery_key"])
    assert rec.status_code == 200, rec.text
    assert live_dek() == dek and status(protected, rec.json())["derived_key_fingerprints"] == fingerprints
    assert protected.get("/api/patients", headers=h(rec.json())).status_code == 200
    assert unlock(protected).status_code == 401                                   # the forgotten passphrase is gone
    assert unlock(protected, NEW_PASSPHRASE).status_code == 200
    assert recover(protected, reply["recovery_key"], "yet another fictional passphrase").status_code == 200   # still valid
    assert {"recovered", "passphrase_changed"} <= set(actions())


def test_wrong_and_mistyped_recovery_keys(protected, monkeypatch):
    reply = setup(protected)
    other_key = vault.create("x", PASSPHRASE, NOW)[1]
    typo = reply["recovery_key"][:-1] + ("A" if reply["recovery_key"][-1] != "A" else "B")
    r = recover(protected, typo)
    assert r.status_code == 422 and "typing mistake" in r.json()["detail"]        # typos are not counted as guesses
    assert recover(protected, reply["recovery_key"], "short").status_code == 422
    for _ in range(auth.MAX_FAILURES):
        r = recover(protected, other_key)
        assert r.status_code == 401 and r.json() == {"detail": "Incorrect recovery key"}
    assert recover(protected, reply["recovery_key"]).status_code == 429         # shared limit with passphrases
    assert unlock(protected).status_code == 429
    monkeypatch.setattr(auth, "_blocked_until", 0.0)
    assert recover(protected, reply["recovery_key"]).status_code == 200


def test_changing_the_passphrase(protected):
    reply = setup(protected)
    confirm(protected, reply)
    dek, key_id = live_dek(), reply["vault"]["key_id"]
    change = lambda current, new: protected.post("/api/vault/passphrase", headers=h(reply),
                                                 json={"current_passphrase": current, "new_passphrase": new})
    assert change("fictional wrong passphrase", NEW_PASSPHRASE).status_code == 401
    assert change(PASSPHRASE, PASSPHRASE).status_code == 422
    assert change(PASSPHRASE, "short").status_code == 422
    ok = change(PASSPHRASE, NEW_PASSPHRASE)
    assert ok.status_code == 200 and ok.json() == {"changed": True, "key_id": key_id}
    assert protected.get("/api/patients", headers=h(reply)).status_code == 200    # the session continues
    assert unlock(protected).status_code == 401
    again = unlock(protected, NEW_PASSPHRASE)
    assert again.status_code == 200 and live_dek() == dek and again.json()["vault"]["key_id"] == key_id
    assert recover(protected, reply["recovery_key"], "third fictional passphrase").status_code == 200
    assert live_dek() == dek
    with db.connection() as conn:
        assert auth._verifier_matches(auth.owner(conn), "third fictional passphrase")
    assert protected.post("/api/vault/passphrase", json={"current_passphrase": PASSPHRASE, "new_passphrase": NEW_PASSPHRASE}).status_code == 401


# 8. Restarts and locking --------------------------------------------------------------------------------------

def test_restart_preserves_the_vault_and_both_ways_in(protected):
    reply = setup(protected)
    confirm(protected, reply)
    dek, fingerprints = live_dek(), status(protected, reply)["derived_key_fingerprints"]
    with TestClient(app) as restarted:
        assert restarted.get("/api/vault/status", headers=h(reply)).status_code == 401
        assert auth._sessions == {}
        after = unlock(restarted).json()
        assert live_dek() == dek and status(restarted, after)["derived_key_fingerprints"] == fingerprints
    with TestClient(app) as again:
        rec = recover(again, reply["recovery_key"])
        assert rec.status_code == 200 and live_dek() == dek


def test_lock_and_expiry_drop_the_key(protected, monkeypatch):
    reply = setup(protected)
    [session] = auth._sessions.values()
    held = session.keys.dek
    protected.post("/api/auth/lock", headers=h(reply))
    assert held == bytearray(32) and session.keys is None
    second = unlock(protected).json()
    [session] = auth._sessions.values()
    held = session.keys.dek
    monkeypatch.setenv("ONCOSCOPE_SESSION_IDLE_SECONDS", "0")
    time.sleep(0.01)
    assert protected.get("/api/patients", headers=h(second)).status_code == 401
    assert held == bytearray(32)
    monkeypatch.setenv("ONCOSCOPE_SESSION_IDLE_SECONDS", "900")
    unlock(protected)                                            # unlocking again replaces (and wipes) the old session
    [old] = auth._sessions.values()
    held = old.keys.dek
    unlock(protected)
    assert held == bytearray(32)


# 6 + Objective 4 through the API: damaged, missing and replaced vaults ------------------------------------------

def test_a_damaged_vault_is_refused_without_changing_anything(protected):
    reply = setup(protected)
    confirm(protected, reply)
    raw = vault_file().read_bytes()
    row_before = owner_row()
    for bad in (raw[: len(raw) // 3], flip(raw, raw.index(b'"ciphertext"') + 20), b"", json.dumps({**json.loads(raw), "version": 9}).encode()):
        vault_file().write_bytes(bad)
        for r in (unlock(protected), recover(protected, reply["recovery_key"])):
            assert r.status_code == 409, r.text
            assert "vault" in r.json()["detail"]
        assert vault_file().read_bytes() == bad and owner_row() == row_before
    vault_file().write_bytes(raw)
    assert unlock(protected).status_code == 200


def test_a_missing_vault_is_never_silently_regenerated(protected):
    reply = setup(protected)
    confirm(protected, reply)
    raw = vault_file().read_bytes()
    vault_file().unlink()
    for r in (unlock(protected), recover(protected, reply["recovery_key"])):
        assert r.status_code == 409 and "missing" in r.json()["detail"]
    assert not vault_file().exists() and owner_row()["vault_key_id"] == reply["vault"]["key_id"]
    vault_file().write_bytes(raw)                                       # restoring the file from a backup fixes it
    assert unlock(protected).status_code == 200


def test_a_replaced_vault_is_refused(protected, tmp_path):
    reply = setup(protected)
    confirm(protected, reply)
    raw = vault_file().read_bytes()
    stranger, _ = vault.create(owner_row()["uid"], PASSPHRASE, NOW)       # same owner id and passphrase, different DEK
    vault.write(vault_file(), stranger.doc, stranger.dek, replace_existing=True)
    r = unlock(protected)
    assert r.status_code == 409 and "does not belong" in r.json()["detail"]
    vault_file().write_bytes(raw)
    assert unlock(protected).status_code == 200


def test_setup_never_replaces_an_existing_vault_file(protected):
    vault_file().parent.mkdir(parents=True, exist_ok=True)
    vault_file().write_bytes(b"FICTIONAL leftover vault")
    r = protected.post("/api/auth/setup", json={"display_name": "Fictional Owner", "passphrase": PASSPHRASE})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    assert vault_file().read_bytes() == b"FICTIONAL leftover vault"
    with db.connection() as conn:
        assert auth.owner(conn) is None


# 9 through the API: interrupted passphrase changes and initialisation ------------------------------------------

def test_interrupted_passphrase_change_before_the_vault_is_replaced(protected, monkeypatch):
    reply = setup(protected)
    confirm(protected, reply)
    raw = vault_file().read_bytes()

    def crash(*_):
        raise OSError("simulated crash")
    real_replace = os.replace
    monkeypatch.setattr(vault.os, "replace", crash)
    r = protected.post("/api/vault/passphrase", headers=h(reply), json={"current_passphrase": PASSPHRASE, "new_passphrase": NEW_PASSPHRASE})
    assert r.status_code == 503
    monkeypatch.setattr(vault.os, "replace", real_replace)
    assert vault_file().read_bytes() == raw and vault.stale_temp_files(vault_file()) == []
    assert unlock(protected, NEW_PASSPHRASE).status_code == 401 and unlock(protected).status_code == 200


def test_interrupted_passphrase_change_after_the_vault_is_replaced(protected, monkeypatch):
    reply = setup(protected)
    confirm(protected, reply)
    dek = live_dek()
    original = auth._store_verifier

    def crash(*_):
        raise OSError("simulated crash after the vault was written")
    monkeypatch.setattr(auth, "_store_verifier", crash)
    with pytest.raises(OSError):
        protected.post("/api/vault/passphrase", headers=h(reply), json={"current_passphrase": PASSPHRASE, "new_passphrase": NEW_PASSPHRASE})
    monkeypatch.setattr(auth, "_store_verifier", original)
    # The vault (authoritative) has the new passphrase; the old verifier is left over and gets re-synchronised.
    assert unlock(protected).status_code == 401
    assert unlock(protected, NEW_PASSPHRASE).status_code == 200 and live_dek() == dek
    assert "verifier_resynced" in actions()
    with db.connection() as conn:
        assert auth._verifier_matches(auth.owner(conn), NEW_PASSPHRASE)


def _legacy_owner(passphrase=PASSPHRASE):
    """An M3.1-A installation: owner and verifier, no vault yet."""
    with db.connection() as conn:
        conn.execute("INSERT INTO local_owner(id, uid, display_name, password_verifier, created_at) VALUES(1,?,?,?,?)",
                     ("fictional-legacy-uid", "Fictional Legacy Owner", auth._hasher.hash(passphrase), NOW))


def test_an_m31a_installation_gets_its_vault_at_the_next_unlock(protected):
    _legacy_owner()
    assert protected.get("/api/auth/status").json()["recovery_available"] is False
    assert recover(protected, vault.create("x", PASSPHRASE, NOW)[1]).status_code == 409
    assert unlock(protected, "fictional wrong passphrase").status_code == 401 and not vault_file().exists()
    reply = unlock(protected).json()
    assert reply["recovery_key"] and owner_row()["vault_id"] == vault.read(vault_file())["vault_id"]
    assert recover(protected, reply["recovery_key"]).status_code == 200
    assert "vault_initialized" in actions()


def test_interrupted_initialisation_is_adopted_not_replaced(protected, monkeypatch):
    _legacy_owner()
    vault_file().parent.mkdir(parents=True, exist_ok=True)
    stale = vault_file().with_name(f".{vault_file().name}.abc123.tmp")
    stale.write_bytes(b"FICTIONAL half-written vault")

    def crash(*_):
        raise OSError("simulated crash before the database recorded the vault")
    real_bind = auth._bind
    monkeypatch.setattr(auth, "_bind", crash)
    with pytest.raises(OSError):
        unlock(protected)
    monkeypatch.setattr(auth, "_bind", real_bind)
    written = vault.read(vault_file())
    assert owner_row()["vault_id"] is None and not stale.exists()
    reply = unlock(protected).json()                       # the file left behind is adopted, not replaced
    assert reply["vault"]["key_id"] == written["key_id"] and owner_row()["vault_id"] == written["vault_id"]
    assert reply["recovery_key"]                            # never confirmed, so a key is issued now
    assert "vault_adopted" in actions()


# 7. No plaintext secrets on disk ----------------------------------------------------------------------------

def test_no_plaintext_secrets_are_written_anywhere(protected, tmp_path):
    reply = setup(protected)
    first_key = reply["recovery_key"]
    second = unlock(protected).json()
    confirm(protected, second)
    protected.post("/api/vault/passphrase", headers=h(second), json={"current_passphrase": PASSPHRASE, "new_passphrase": NEW_PASSPHRASE})
    rec = recover(protected, second["recovery_key"], "third fictional passphrase").json()
    protected.post("/api/patients", headers=h(rec), json={"label": "Fictional Vault Case"})
    dek = live_dek()
    exported = protected.get("/api/patients/1/export.json", headers=h(rec)).text
    backup_dir = backup(tmp_path / "backups")
    secrets_ = [dek, dek.hex().encode(), base64.b64encode(dek), PASSPHRASE.encode(), NEW_PASSPHRASE.encode(),
                b"third fictional passphrase"]
    for key in (first_key, second["recovery_key"]):
        raw = vault.parse_recovery_key(key)
        secrets_ += [key.encode(), key.replace("-", "").encode(), raw, raw.hex().encode(), base64.b64encode(raw)]
    secrets_ += [vault.derive(dek, p) for p in vault.PURPOSES]
    files = [p for p in Path(os.environ["ONCOSCOPE_DB_PATH"]).parent.rglob("*") if p.is_file()]
    files += [p for p in backup_dir.rglob("*") if p.is_file()]
    assert any(p.name == "oncoscope-vault.json" for p in files)
    for path in files:
        data = path.read_bytes()
        for secret in secrets_:
            assert secret not in data, (path.name, secret[:6])
    for secret in secrets_:
        assert secret.decode("latin-1") not in exported


# 10-11. M3.1-A behaviour and demonstration mode ---------------------------------------------------------------

def test_vault_routes_need_a_session(protected):
    setup(protected)
    for method, path in (("GET", "/api/vault/status"), ("POST", "/api/vault/passphrase"), ("POST", "/api/vault/recovery-key/confirm")):
        assert protected.request(method, path, json={}).status_code == 401
    r = protected.post("/api/auth/recover", json={"recovery_key": "x", "new_passphrase": NEW_PASSPHRASE},
                       headers={"Origin": "https://attacker.example"})
    assert r.status_code == 403


def test_demonstration_mode_has_no_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "demo" / "demo.sqlite3"))
    with TestClient(app) as demo:
        assert demo.get("/api/auth/status").json()["recovery_available"] is False
        assert recover(demo, "x").status_code == 409
        for method, path in (("GET", "/api/vault/status"), ("POST", "/api/vault/recovery-key/confirm")):
            assert demo.request(method, path).status_code == 409
        assert demo.post("/api/vault/passphrase", json={"current_passphrase": PASSPHRASE, "new_passphrase": NEW_PASSPHRASE}).status_code == 409
        demo.post("/api/patients", json={"label": "Fictional demo"})
    assert not (tmp_path / "demo" / "oncoscope-vault.json").exists()


# Backups and restores carry the vault --------------------------------------------------------------------------

def test_backups_carry_the_vault_and_restores_never_replace_one(protected, tmp_path, monkeypatch):
    reply = setup(protected)
    confirm(protected, reply)
    protected.post("/api/patients", headers=h(reply), json={"label": "Fictional Backup Case"})
    dek = live_dek()
    backup_dir = backup(tmp_path / "backups")
    assert (backup_dir / "oncoscope-vault.json").read_bytes() == vault_file().read_bytes()
    assert verify_backup(backup_dir)["ok"]
    (backup_dir / "oncoscope-vault.json").write_bytes(b"FICTIONAL damaged")
    assert not verify_backup(backup_dir)["ok"]
    (backup_dir / "oncoscope-vault.json").write_bytes(vault_file().read_bytes())

    # Restore onto a clean folder, then recover with the recovery key alone: same DEK.
    target = tmp_path / "elsewhere" / "oncoscope.sqlite3"
    result = restore(backup_dir, target)
    assert result["consistent"], result["differences"]
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(target))
    with TestClient(app) as moved:
        assert recover(moved, reply["recovery_key"]).status_code == 200 and live_dek() == dek

    # A different vault already at the target is never replaced.
    other = tmp_path / "third" / "oncoscope.sqlite3"
    other.parent.mkdir()
    (other.parent / "oncoscope-vault.json").write_bytes(b"FICTIONAL another installation's vault")
    with pytest.raises(RestoreError, match="different key vault"):
        restore(backup_dir, other)
    assert (other.parent / "oncoscope-vault.json").read_bytes() == b"FICTIONAL another installation's vault"
    assert not other.exists()


# 12-ish. Logs of a real server ------------------------------------------------------------------------------

def test_server_logs_contain_no_recovery_keys_or_passphrases(tmp_path):
    from test_security import _free_port
    port, log_path = _free_port(), tmp_path / "server.log"
    env = {**os.environ, "ONCOSCOPE_MODE": "protected", "ONCOSCOPE_DB_PATH": str(tmp_path / "logcheck.sqlite3")}
    for key in ("ONCOSCOPE_DOCUMENT_DIR", "ONCOSCOPE_VAULT_PATH"):
        env.pop(key, None)
    with open(log_path, "w") as log:
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                                cwd=APP_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            c = httpx.Client(base_url=f"http://127.0.0.1:{port}/api")
            for _ in range(100):
                try:
                    c.get("/health")
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            reply = c.post("/auth/setup", json={"display_name": "Fictional Owner", "passphrase": PASSPHRASE}).json()
            c.post("/vault/recovery-key/confirm", headers=h(reply))
            c.post("/vault/passphrase", headers=h(reply), json={"current_passphrase": PASSPHRASE, "new_passphrase": NEW_PASSPHRASE})
            c.post("/auth/recover", json={"recovery_key": reply["recovery_key"][:-1] + "Z", "new_passphrase": "x" * 20})
            rec = c.post("/auth/recover", json={"recovery_key": reply["recovery_key"], "new_passphrase": "third fictional passphrase"}).json()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    text = log_path.read_text()
    assert "POST /api/auth/recover" in text
    for secret in (reply["recovery_key"], reply["recovery_key"].replace("-", ""), PASSPHRASE, NEW_PASSPHRASE,
                   "third fictional passphrase", reply["token"], rec["token"]):
        assert secret not in text
