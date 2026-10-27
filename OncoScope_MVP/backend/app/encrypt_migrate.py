"""Encrypts an existing plaintext protected-mode database with SQLCipher: `python -m app.encrypt_migrate`.

Fictional data only. Stop the OncoScope server first (it refuses a plaintext protected database anyway, so it cannot
write to it during the migration).

  python -m app.encrypt_migrate                   # unlock with the owner passphrase
  python -m app.encrypt_migrate --recovery-key    # unlock with the recovery key instead
  python -m app.encrypt_migrate --display-name "Fictional Owner"   # a database from before M3.1-A (no owner yet)
  python -m app.encrypt_migrate --rollback        # abandon an interrupted run before activation

Workflow. Every step is recorded in `encrypt-migration.json` beside the database, so running the tool again after an
interruption finishes (or, with --rollback, abandons) the migration deterministically. While that file exists the
server refuses to unlock.
1. Checks: protected mode; the database is plaintext SQLite with a known schema, and every case is marked synthetic.
2. Authentication with the key vault (passphrase, or recovery key) before anything is changed. An M3.1-B vault (v1) is
   upgraded to v2. A database from before M3.1-B gets its vault now. An M3.1-A owner's passphrase is checked with the old
   verifier; a database without an owner gets one (--display-name). A new vault's recovery key is shown once.
3. Source verification: integrity_check, foreign_key_check, the audit hash chain, and a fingerprint of every table
   (row count + content hash), every schema object (tables, indexes, triggers), user_version and the audit head.
4. A verified plaintext backup (app.backup), reused by a re-run only while it still matches the source.
5. `sqlcipher_export` into `<database>.encrypting`, keyed with `vault.derive(dek, "database")`.
6. Verification of the encrypted copy: the same fingerprint, integrity_check, cipher_integrity_check, foreign keys, the
   audit chain, and a byte scan for every text value of the source (none may appear).
7. Activation: the vault records "encrypted", `os.replace` swaps the verified copy in (atomic; the plaintext original
   still exists as the verified backup), a `database_encrypted` audit entry is appended, the state file is removed.

The plaintext backup made in step 4 stays unencrypted and is as sensitive as the original. This tool does not delete
it, and nothing here claims secure deletion (see docs/M3_1C_DATABASE_ENCRYPTION.md for how to retire it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import auth, vault
from .backup import backup as make_backup, verify_backup
from .credentials import CredentialError, ask_secret
from .db import (
    LAST_PLAINTEXT_SCHEMA, SQLCIPHER_VERSION, DatabaseRefused, _connect, app_mode, audit, audit_head, current_actor,
    database_path, document_dir, file_kind, migrate, migration_state_path, now_utc, open_encrypted, vault_path,
    verify_audit_chain,
)

STATE_FORMAT, STATE_VERSION = "oncoscope-encrypt-migration", 1
SCAN_MIN_BYTES = 6           # shorter values could occur by chance in random ciphertext
_legacy_hasher = PasswordHasher()   # only to check an M3.1-A owner's verifier, and to fill the column for a new owner

Ask = Callable[[str], str]   # "passphrase" | "recovery_key" | "new_passphrase" -> the secret


class MigrationError(RuntimeError):
    pass


def _checkpoint(step: str) -> None:
    """Interruption tests replace this to stop a run right after `step`."""


def staging_path() -> Path:
    path = database_path()
    return path.with_name(path.name + ".encrypting")


# --- state file --------------------------------------------------------------------------------------

def read_state() -> dict | None:
    path = migration_state_path()
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("format") != STATE_FORMAT or state.get("version") != STATE_VERSION:
            raise ValueError
    except (ValueError, UnicodeDecodeError, AttributeError):
        raise MigrationError(f"The migration record {path} is damaged. Nothing was changed; inspect it (it holds no "
                             "secrets) and the database files before deciding how to continue.") from None
    return state


def _save_state(state: dict, step: str) -> None:
    state.update(step=step, updated_at=now_utc())
    vault.write_raw(migration_state_path(), json.dumps(state, indent=2, sort_keys=True).encode("utf-8"))


# --- fingerprints --------------------------------------------------------------------------------------

def _tables(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def fingerprint(conn) -> dict:
    """Everything a lossless migration must preserve, as hashes and counts (no record content)."""
    tables = {}
    for table in _tables(conn):
        rows = [list(r) for r in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
        tables[table] = {"rows": len(rows),
                         "sha256": hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()}
    schema = [list(r) for r in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")]
    kinds: dict[str, int] = {}
    for kind, *_ in schema:
        kinds[kind] = kinds.get(kind, 0) + 1
    return {"user_version": conn.execute("PRAGMA user_version").fetchone()[0], "tables": tables,
            "schema_sha256": hashlib.sha256(json.dumps(schema).encode()).hexdigest(), "schema_objects": kinds,
            "audit_head": audit_head(conn), "audit_chain": verify_audit_chain(conn)}


def _health(conn) -> list[str]:
    problems = []
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        problems.append(f"integrity_check: {integrity}")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        problems.append("foreign_key_check found broken references")
    chain = verify_audit_chain(conn)
    if not chain["valid"]:
        problems.append(f"audit chain invalid at entry {chain['first_invalid_id']}")
    return problems


def _text_values(conn) -> set[bytes]:
    values = set()
    for table in _tables(conn):
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            values.update(v.encode("utf-8") for v in row if isinstance(v, str) and len(v.encode("utf-8")) >= SCAN_MIN_BYTES)
    return values


def plaintext_found(path: Path, values: set[bytes]) -> list[str]:
    """Source text values that appear verbatim in a file (an encrypted database must contain none)."""
    data = path.read_bytes()
    found = [v for v in values if v in data]
    if data.startswith(b"SQLite format 3\x00"):
        found.insert(0, b"SQLite format 3 header")
    return [v[:40].decode("utf-8", "replace") for v in found]


# --- authentication ------------------------------------------------------------------------------------

def _actor(doc_or_row: dict) -> tuple[str, str]:
    uid = doc_or_row.get("owner_uid") or doc_or_row["uid"]
    return f"owner:{uid}", doc_or_row.get("owner_display_name") or doc_or_row["display_name"]


def _open(doc: dict, ask: Ask, use_recovery_key: bool) -> vault.Opened:
    try:
        if use_recovery_key:
            return vault.open_with_recovery_key(doc, ask("recovery_key"))
        return vault.open_with_passphrase(doc, ask("passphrase"))
    except vault.WrongCredential as exc:
        if str(exc) in ("format", "checksum"):
            raise MigrationError("That is not a valid recovery key (typing mistake?). Nothing was changed.") from None
        raise MigrationError(("Incorrect recovery key" if use_recovery_key else "Incorrect passphrase")
                             + ". Nothing was changed.") from None
    except vault.VaultError as exc:
        raise MigrationError(exc.message) from None


def _verifier_matches(row: dict, passphrase: str) -> bool:
    try:
        return _legacy_hasher.verify(row["password_verifier"], passphrase)
    except (VerifyMismatchError, VerificationError, InvalidHashError, KeyError):
        return False


def _authenticate_plaintext(conn, ask: Ask, display_name: str | None, use_recovery_key: bool,
                            resuming: bool) -> tuple[vault.Opened, str | None, list]:
    """Opens (or, for databases from before M3.1-B, first creates) the vault. Nothing is written to the database until
    a credential has been checked, except that a database without any owner gets one: that is creating a credential."""
    tables = set(_tables(conn))
    row = auth.owner(conn) if "local_owner" in tables else None
    path = vault_path()
    events: list = []
    recovery_key = None
    if path.exists():
        try:
            doc = vault.read(path)
        except vault.VaultError as exc:
            raise MigrationError(exc.message) from None
        if row is None:
            raise MigrationError("A key vault is in the data folder but this database has no owner. Nothing was changed; "
                                 "move the vault away only if it belongs to another installation.")
        if row.get("vault_id") is None:   # left behind by an interrupted initialisation (M3.1-B): adopt, never replace
            if doc["owner_uid"] != row["uid"]:
                raise MigrationError("The key vault in the data folder belongs to a different owner. Nothing was changed.")
            opened = _open(doc, ask, use_recovery_key)
            events.append(("vault_adopted", {"vault_id": doc["vault_id"], "key_id": doc["key_id"]}))
        else:
            if (doc["vault_id"], doc["key_id"], doc["owner_uid"]) != (row["vault_id"], row["vault_key_id"], row["uid"]):
                raise MigrationError("The key vault does not belong to this database (it was replaced). Nothing was changed.")
            opened = _open(doc, ask, use_recovery_key)
        if vault.database_state(opened.doc) == "encrypted" and not resuming:
            opened.wipe()
            raise MigrationError("The vault says this installation's database is encrypted, but the database file is "
                                 "plaintext. It may have been replaced; nothing was changed.")
    else:
        if row is not None and row.get("vault_id"):
            raise MigrationError("The key vault file is missing. Restore it from a backup; OncoScope will not create a new "
                                 "key for an installation that already has one. Nothing was changed.")
        if use_recovery_key:
            raise MigrationError("This installation has no key vault yet, so there is no recovery key. Use the passphrase.")
        if row is None:
            if not display_name:
                raise MigrationError("This database has no owner yet. Run again with --display-name to create one.")
            passphrase = ask("new_passphrase")
            try:
                auth.check_passphrase_policy(passphrase)
            except auth.AuthError as exc:
                raise MigrationError(exc.detail) from None
        else:
            passphrase = ask("passphrase")
            if not _verifier_matches(row, passphrase):
                raise MigrationError("Incorrect passphrase. Nothing was changed.")
        conn.isolation_level = None
        migrate(conn, up_to=LAST_PLAINTEXT_SCHEMA)   # local_owner and its vault columns (what the M3.1-B server did)
        conn.isolation_level = ""
        if row is None:
            conn.execute("INSERT INTO local_owner(id, uid, display_name, password_verifier, created_at) VALUES(1,?,?,?,?)",
                         (str(uuid.uuid4()), display_name, _legacy_hasher.hash(passphrase), now_utc()))
            conn.commit()
            row = auth.owner(conn)
            events.append(("owner_setup", {"owner_uid": row["uid"], "display_name": row["display_name"]}))
        for leftover in vault.stale_temp_files(path):
            leftover.unlink(missing_ok=True)
        opened, recovery_key = vault.create(row["uid"], passphrase, now_utc(), display_name=row["display_name"],
                                            database_state="plaintext")
        vault.write(path, opened.doc, opened.dek, replace_existing=False)
        events += [("vault_initialized", {"vault_id": opened.doc["vault_id"], "key_id": opened.doc["key_id"],
                                          "kdf": "argon2id", "wrap": "aes-256-gcm", "vault_version": opened.doc["version"]}),
                   ("recovery_key_issued", {"vault_id": opened.doc["vault_id"]})]
    conn.isolation_level = None
    migrate(conn, up_to=LAST_PLAINTEXT_SCHEMA)
    conn.isolation_level = ""
    row = auth.owner(conn)
    if row["vault_id"] is None:
        conn.execute("UPDATE local_owner SET vault_id=?, vault_key_id=?, updated_at=? WHERE id=1",
                     (opened.doc["vault_id"], opened.doc["key_id"], now_utc()))
    if opened.doc["version"] == 1:
        doc = vault.upgraded(opened, row["display_name"], now_utc())
        vault.write(path, doc, opened.dek, replace_existing=True)
        opened.doc = doc
        events.append(("vault_upgraded", {"vault_id": doc["vault_id"], "from_version": 1, "to_version": doc["version"]}))
    return opened, recovery_key, events


def _record(conn, actor: tuple[str, str], events: list) -> None:
    marker = current_actor.set(actor)
    try:
        for action, detail in events:
            audit(conn, "local_owner", 1, action, detail)
    finally:
        current_actor.reset(marker)
    conn.commit()


# --- migration -----------------------------------------------------------------------------------------

def _export(conn, staging: Path, key: bytes, user_version: int) -> None:
    staging.unlink(missing_ok=True)
    Path(f"{staging}-journal").unlink(missing_ok=True)
    conn.isolation_level = None
    conn.execute(f"ATTACH DATABASE ? AS encrypted KEY \"x'{bytes(key).hex()}'\"", (str(staging),))
    try:
        conn.execute("PRAGMA encrypted.cipher_memory_security = OFF")
        conn.execute("SELECT sqlcipher_export('encrypted')")
        conn.execute(f"PRAGMA encrypted.user_version = {int(user_version)}")
    finally:
        conn.execute("DETACH DATABASE encrypted")


def _verify_encrypted(path: Path, key: bytes, expected: dict, values: set[bytes]) -> list[str]:
    if file_kind(path) != "encrypted":
        return ["the copy is not an encrypted database"]
    try:
        conn = open_encrypted(path, key)
    except DatabaseRefused as exc:
        return [exc.message]
    try:
        problems = _health(conn)
        if conn.execute("PRAGMA cipher_integrity_check").fetchall():
            problems.append("cipher_integrity_check reported damaged pages")
        found = fingerprint(conn)
    finally:
        conn.close()
    for part in ("user_version", "schema_sha256", "schema_objects", "audit_head", "audit_chain"):
        if found[part] != expected[part]:
            problems.append(f"{part} differs: {found[part]} != {expected[part]}")
    for table, info in expected["tables"].items():
        if found["tables"].get(table) != info:
            problems.append(f"table {table} differs: {found['tables'].get(table)} != {info}")
    if set(found["tables"]) != set(expected["tables"]):
        problems.append(f"tables differ: {sorted(set(found['tables']) ^ set(expected['tables']))}")
    leaked = plaintext_found(path, values)
    if leaked:
        problems.append(f"{len(leaked)} plaintext value(s) found in the encrypted file, e.g. {leaked[0]!r}")
    return problems


def _finish(opened: vault.Opened, key: bytes, state: dict) -> dict:
    """After the swap: confirm the active database is the verified copy, append the audit entry, clear the state."""
    path = database_path()
    conn = open_encrypted(path, key)
    try:
        done = conn.execute("SELECT id FROM audit_log WHERE action='database_encrypted' AND detail_json LIKE ?",
                            (f'%"migration_id": "{state["migration_id"]}"%',)).fetchone()
        if not done:
            found = fingerprint(conn)
            if found["tables"] != state["source"]["tables"] or found["audit_head"] != state["source"]["audit_head"]:
                raise MigrationError("The encrypted database in place does not match the verified copy. Nothing more was "
                                     f"changed; the plaintext backup is at {state.get('backup')}.")
            totals = state["source"]["tables"]
            _record(conn, _actor(opened.doc), [("database_encrypted", {
                "migration_id": state["migration_id"], "cipher": "sqlcipher4", "sqlcipher_version": SQLCIPHER_VERSION,
                "key_fingerprint": opened.doc["database"]["key_fingerprint"], "schema_version": state["source"]["user_version"],
                "tables": len(totals), "rows": sum(t["rows"] for t in totals.values()),
                "source_audit_head": state["source"]["audit_head"], "plaintext_backup": state.get("backup_name")})])
            _checkpoint("audited")
    finally:
        conn.close()
    if vault.database_state(opened.doc) != "encrypted":
        doc = vault.with_database_state(opened, "encrypted", now_utc(), migrated=True)
        vault.write(vault_path(), doc, opened.dek, replace_existing=True)
        opened.doc = doc
    for leftover in (staging_path(), Path(f"{staging_path()}-journal")):
        leftover.unlink(missing_ok=True)
    migration_state_path().unlink(missing_ok=True)
    return {"status": "encrypted", "database": str(path), "migration_id": state["migration_id"],
            "tables": len(state["source"]["tables"]), "rows": sum(t["rows"] for t in state["source"]["tables"].values()),
            "audit_head": state["source"]["audit_head"], "plaintext_backup": state.get("backup"),
            "sqlcipher_version": SQLCIPHER_VERSION}


def _vault_for_encrypted(ask: Ask, use_recovery_key: bool) -> tuple[vault.Opened, bytearray]:
    try:
        doc = vault.read(vault_path())
    except vault.VaultError as exc:
        raise MigrationError(exc.message) from None
    if doc["version"] < 2:
        raise MigrationError("The key vault predates database encryption; the encrypted database cannot be its own.")
    opened = _open(doc, ask, use_recovery_key)
    return opened, bytearray(vault.derive(opened.dek, "database"))


def run(ask: Ask, *, display_name: str | None = None, use_recovery_key: bool = False,
        on_recovery_key: Callable[[vault.Opened, str], None] | None = None) -> dict:
    """Migrates (or finishes migrating) the configured protected database. Raises MigrationError, changing nothing
    further, at the first failed check."""
    if app_mode() != "protected":
        raise MigrationError("Run this in protected mode; demonstration databases stay unencrypted.")
    path, staging = database_path(), staging_path()
    state = read_state()
    kind = file_kind(path)

    if kind == "encrypted":
        if state is None:
            return {"status": "already_encrypted", "database": str(path)}
        opened, key = _vault_for_encrypted(ask, use_recovery_key)   # interrupted after the swap: finish
        try:
            return _finish(opened, key, state)
        finally:
            opened.wipe()
    if kind in ("missing", "empty"):
        if state is None or not staging.exists():
            raise MigrationError(f"There is no database at {path} to migrate.")
        opened, key = _vault_for_encrypted(ask, use_recovery_key)   # interrupted during the swap: the copy is complete
        try:
            problems = _verify_encrypted(staging, key, state["source"], set())
            if problems:
                raise MigrationError("The database file is missing and the encrypted copy does not verify: "
                                     + "; ".join(problems) + f". Restore the plaintext backup at {state.get('backup')}.")
            os.replace(staging, path)
            return _finish(opened, key, state)
        finally:
            opened.wipe()

    conn = _connect(path)
    opened = None
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > LAST_PLAINTEXT_SCHEMA:
            raise MigrationError(f"This plaintext database has schema v{version}, which no plaintext protected "
                                 "installation ever had (a demonstration database?). Nothing was changed.")
        if "patients" in _tables(conn) and conn.execute("SELECT count(*) FROM patients WHERE is_synthetic != 1").fetchone()[0]:
            raise MigrationError("This database contains records not marked synthetic. This tool migrates fictional "
                                 "databases only; nothing was changed.")
        opened, recovery_key, events = _authenticate_plaintext(conn, ask, display_name, use_recovery_key, state is not None)
        if recovery_key and on_recovery_key:
            on_recovery_key(opened, recovery_key)
        migration_id = state["migration_id"] if state else str(uuid.uuid4())
        if state is None:
            events.append(("database_encryption_started", {"migration_id": migration_id, "cipher": "sqlcipher4",
                                                           "sqlcipher_version": SQLCIPHER_VERSION}))
        _record(conn, _actor(opened.doc), events)
        key = bytearray(vault.derive(opened.dek, "database"))

        problems = _health(conn)
        if problems:
            raise MigrationError("The plaintext database does not verify, so it was not migrated: " + "; ".join(problems))
        source = fingerprint(conn)
        values = _text_values(conn)
        if state is not None and state["source"] != source:
            state["backup"] = None   # the database changed since the interrupted run: take a fresh backup
        state = state or {"format": STATE_FORMAT, "version": STATE_VERSION, "migration_id": migration_id,
                          "started_at": now_utc(), "vault_id": opened.doc["vault_id"], "backup": None}
        state.update(source=source, key_fingerprint=opened.doc["database"]["key_fingerprint"])
        _save_state(state, "started")
        _checkpoint("started")

        reusable = state.get("backup") and Path(state["backup"]).exists() and verify_backup(Path(state["backup"]))["ok"]
        if not reusable:
            try:
                target = make_backup(path.parent / "backups", db_path=path, doc_root=document_dir(), label="pre-encryption")
            except SystemExit as exc:
                raise MigrationError(f"The pre-migration backup failed, so nothing was migrated: {exc}") from None
            check = verify_backup(target)
            if not check["ok"]:
                raise MigrationError("The pre-migration backup does not verify: " + "; ".join(check["problems"]))
            state.update(backup=str(target), backup_name=target.name)
        _save_state(state, "backed_up")
        _checkpoint("backed_up")

        _export(conn, staging, key, source["user_version"])
        _save_state(state, "exported")
        _checkpoint("exported")
    except BaseException:
        if opened is not None:
            opened.wipe()
        raise
    finally:
        conn.close()   # before the swap: Windows cannot replace an open file

    try:
        problems = _verify_encrypted(staging, key, source, values)
        if problems:
            staging.unlink(missing_ok=True)
            raise MigrationError("The encrypted copy did not verify and was discarded; the plaintext database is "
                                 "unchanged: " + "; ".join(problems))
        state["staging_sha256"] = hashlib.sha256(staging.read_bytes()).hexdigest()
        _save_state(state, "verified")
        _checkpoint("verified")

        _save_state(state, "activating")
        if vault.database_state(opened.doc) != "encrypted":
            doc = vault.with_database_state(opened, "encrypted", now_utc(), migrated=True)
            vault.write(vault_path(), doc, opened.dek, replace_existing=True)
            opened.doc = doc
        _checkpoint("vault_marked")
        os.replace(staging, path)
        _checkpoint("replaced")
        result = _finish(opened, key, state)
        result["plaintext_values_checked"] = len(values)
        return result
    finally:
        opened.wipe()


def rollback(ask: Ask, *, use_recovery_key: bool = False) -> dict:
    """Abandons an interrupted migration that has not swapped the database yet: the plaintext database stays in use."""
    state = read_state()
    if state is None:
        raise MigrationError("There is no interrupted migration to roll back.")
    if file_kind(database_path()) != "plaintext":
        raise MigrationError("The encrypted database is already in place, so the migration cannot be rolled back. "
                             "Run the tool again (without --rollback) to finish it.")
    try:
        doc = vault.read(vault_path())
    except vault.VaultError as exc:
        raise MigrationError(exc.message) from None
    opened = _open(doc, ask, use_recovery_key)
    try:
        if vault.database_state(opened.doc) == "encrypted":
            vault.write(vault_path(), vault.with_database_state(opened, "plaintext", now_utc()), opened.dek,
                        replace_existing=True)
    finally:
        opened.wipe()
    for leftover in (staging_path(), Path(f"{staging_path()}-journal")):
        leftover.unlink(missing_ok=True)
    migration_state_path().unlink()
    return {"status": "rolled_back", "database": str(database_path()), "plaintext_backup": state.get("backup")}


# --- command line ----------------------------------------------------------------------------------------

PLAINTEXT_WARNING = """
WARNING: the pre-migration backup is NOT encrypted:
  {backup}
It holds the same fictional records as the old database, in readable form. Keep it only until you have checked the
encrypted installation, then retire it (see docs/M3_1C_DATABASE_ENCRYPTION.md). Deleting a file does not reliably erase
its contents from the disk; full-disk encryption is what protects deleted and copied files."""


def _cli_ask(use_terminal_confirmation: bool) -> Ask:
    def ask(kind: str) -> str:
        if kind == "new_passphrase":
            first = ask_secret("New owner passphrase (at least 12 characters): ")
            if use_terminal_confirmation and ask_secret("Repeat the passphrase: ") != first:
                raise CredentialError("the two passphrases do not match")
            return first
        return ask_secret("Recovery key: " if kind == "recovery_key" else "Owner passphrase: ")
    return ask


def _show_recovery_key(opened: vault.Opened, recovery_key: str) -> None:
    """On a terminal: shown once and confirmed by its last group. Otherwise never printed: OncoScope then issues and
    shows a fresh one at the first unlock (an unconfirmed key is always replaced)."""
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        print("A recovery key was created. It is not printed here (no terminal); OncoScope will show you a new one when "
              "you first unlock.")
        return
    print("\nRECOVERY KEY — the only other way in if you forget the passphrase. Write it down and keep it apart from "
          f"this computer:\n\n    {recovery_key}\n")
    if input("Type the last group of the key to confirm you saved it: ").strip().upper() == recovery_key.split("-")[-1]:
        doc = vault.with_recovery_confirmed(opened, now_utc())
        vault.write(vault_path(), doc, opened.dek, replace_existing=True)
        opened.doc = doc
    else:
        print("Not confirmed: OncoScope will issue and show a new recovery key at the next unlock.")
    print("\033[2J\033[H", end="")   # clear the screen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Encrypt OncoScope's plaintext protected-mode database with SQLCipher "
                                                 "(fictional data only; stop the server first)")
    parser.add_argument("--recovery-key", action="store_true", help="unlock with the recovery key instead of the passphrase")
    parser.add_argument("--display-name", help="owner name, only for a database that has no owner yet")
    parser.add_argument("--rollback", action="store_true", help="abandon an interrupted migration before activation")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args(argv)
    interactive = sys.stdin.isatty()
    try:
        if args.rollback:
            result = rollback(_cli_ask(interactive), use_recovery_key=args.recovery_key)
        else:
            if not args.yes and interactive and input(
                    f"Encrypt {database_path()} (fictional data only) with SQLCipher? Type MIGRATE to continue: ") != "MIGRATE":
                print("Nothing was changed.")
                return 1
            result = run(_cli_ask(interactive), display_name=args.display_name, use_recovery_key=args.recovery_key,
                         on_recovery_key=_show_recovery_key)
    except (MigrationError, CredentialError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, default=str))
    if result.get("plaintext_backup"):
        print(PLAINTEXT_WARNING.format(backup=result["plaintext_backup"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
