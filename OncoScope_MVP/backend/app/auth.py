"""Protected local access: one local owner, a key vault opened by the owner's passphrase or recovery key, an encrypted
database opened with a key derived from the vault, and short-lived in-memory sessions.

Order (M3.1-C): nothing protected is read before unlock. What the lock screen needs (is there an owner, is the
database encrypted, is a migration pending) comes from the vault header and the files on disk, never from the
database. Unlocking opens the vault first (AES-GCM authenticates the passphrase or recovery key and the MAC
authenticates the header), and only then derives the SQLCipher key and opens the database. A wrong credential therefore
never reaches SQLCipher. Locking, expiry, a new unlock and a server restart wipe the session's DEK and database key.

Failed attempts happen while the database is locked, so they cannot be written to the audit log at that moment. They are
counted in memory and recorded with the next successful unlock (`failed_attempts_before_unlock`); a server restart
forgets the count (documented in docs/M3_1C_DATABASE_ENCRYPTION.md).

Argon2id comes from argon2-cffi, AES-GCM/HKDF from pyca/cryptography (see vault.py), SQLCipher from sqlcipher3 (db.py),
tokens from `secrets`. The original M3.1-A passphrase verifier is retired by schema migration 6.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field

from . import vault
from .db import (
    SQLCIPHER_VERSION, DatabaseRefused, app_mode, database_path, file_kind, migrate, migration_state_path, now_utc,
    open_encrypted, sqlite, vault_path,
)

PASSPHRASE_MIN, PASSPHRASE_MAX = 12, 1024
MAX_FAILURES = 5                  # wrong passphrases or recovery keys allowed before unlocking is refused for a while
LOCKOUT_BASE_SECONDS = 30         # doubles with each further failure ...
LOCKOUT_MAX_SECONDS = 15 * 60     # ... up to 15 minutes

VAULT_STATUS = {"missing": 409, "damaged": 409, "tampered": 409, "unsupported": 409, "foreign": 409, "exists": 409,
                "mismatch": 409, "write_failed": 503}


def idle_seconds() -> int:
    """A session ends after this long without a request (default 15 minutes)."""
    return int(os.environ.get("ONCOSCOPE_SESSION_IDLE_SECONDS", "900"))


def max_seconds() -> int:
    """A session ends this long after unlocking, however active (default 8 hours)."""
    return int(os.environ.get("ONCOSCOPE_SESSION_MAX_SECONDS", str(8 * 3600)))


def _wipe(buffer: bytearray | None) -> None:
    if buffer is not None:
        for i in range(len(buffer)):
            buffer[i] = 0


@dataclass
class Session:
    actor: tuple[str, str]
    created: float
    last_seen: float
    keys: vault.Opened | None = None    # the unlocked vault; wiped when the session ends
    db_key: bytearray | None = None     # SQLCipher key derived from the DEK; wiped when the session ends


@dataclass
class Unlocked:
    """Result of setup, unlock or recovery: who, the opened vault and database key, a recovery key to show once, and
    the audit events to record once the database is open."""
    owner: dict
    opened: vault.Opened
    db_key: bytearray
    recovery_key: str | None = None
    events: list[tuple[str, dict]] = field(default_factory=list)


class AuthError(Exception):
    def __init__(self, status: int, detail: str, retry_after: int | None = None):
        super().__init__(detail)
        self.status, self.detail, self.retry_after = status, detail, retry_after


_lock = threading.Lock()
_vault_lock = threading.Lock()   # one vault or database-opening change at a time
# Keyed by SHA-256 of the token, so the table itself never holds a usable token. Lives only in this process's memory:
# restarting the server ends every session and forgets every key.
_sessions: dict[str, Session] = {}
_failures = 0
_blocked_until = 0.0
_failed_while_locked = 0


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _drop(session: Session) -> None:
    if session.keys is not None:
        session.keys.wipe()
        session.keys = None
    _wipe(session.db_key)
    session.db_key = None


def reset() -> None:
    """Forget all sessions, keys and failure counts (server start, and tests)."""
    global _failures, _blocked_until, _failed_while_locked
    with _lock:
        for s in _sessions.values():
            _drop(s)
        _sessions.clear()
        _failures, _blocked_until, _failed_while_locked = 0, 0.0, 0


def owner(conn) -> dict | None:
    row = conn.execute("SELECT * FROM local_owner WHERE id=1").fetchone()
    return dict(row) if row else None


def actor_of(owner_row: dict) -> tuple[str, str]:
    return f"owner:{owner_row['uid']}", owner_row["display_name"]


def check_passphrase_policy(passphrase: str) -> None:
    if not PASSPHRASE_MIN <= len(passphrase) <= PASSPHRASE_MAX:
        raise AuthError(422, f"Choose a passphrase of at least {PASSPHRASE_MIN} characters")


def vault_error(exc: vault.VaultError) -> AuthError:
    return AuthError(VAULT_STATUS.get(exc.code, 409), exc.message)


# --- what is on disk (no secrets, no database reads) -------------------------------------------------

def storage() -> dict:
    """The installation's state, decided from the vault header and the database file's kind alone. Never creates or
    changes anything. States: setup, locked, migration_required, migration_interrupted, refused."""
    def state(name: str, message: str = "", doc: dict | None = None) -> dict:
        return {"state": name, "message": message, "vault": doc}

    if migration_state_path().exists():
        return state("migration_interrupted", "A database encryption migration did not finish. Run "
                                              "`python -m app.encrypt_migrate` again to complete it or roll it back.")
    db_kind = file_kind(database_path())
    path = vault_path()
    if not path.exists():
        if db_kind in ("missing", "empty"):
            return state("setup")
        if db_kind == "plaintext":
            return state("migration_required", "This installation's database is not encrypted yet. Stop OncoScope "
                                               "and run `python -m app.encrypt_migrate` to encrypt it.")
        return state("refused", "The key vault file is missing, so the encrypted database cannot be opened. Restore the "
                                "vault from a backup; OncoScope will not create a new key for existing data.")
    try:
        doc = vault.read(path)
    except vault.VaultError as exc:
        return state("refused", exc.message)
    db_state = vault.database_state(doc)
    if db_state == "plaintext":
        if db_kind == "plaintext":
            return state("migration_required", "This installation's database is not encrypted yet. Stop OncoScope "
                                               "and run `python -m app.encrypt_migrate` to encrypt it.", doc)
        return state("refused", "The key vault expects an unencrypted database that is not there. Nothing was changed; "
                                "restore the database from a backup.", doc)
    if db_kind == "plaintext":
        return state("refused", "The database file is not encrypted although this installation's database is. It may "
                                "have been replaced; nothing was opened or changed.", doc)
    if db_kind in ("missing", "empty") and db_state == "encrypted":
        return state("refused", "The encrypted database file is missing or empty. Restore it from a backup; OncoScope "
                                "will not create a new database for an existing installation.", doc)
    return state("locked", "", doc)


def status() -> dict:
    """Enough for the lock screen; nothing about cases or the owner."""
    if app_mode() != "protected":
        return {"mode": app_mode(), "setup_required": False, "recovery_available": False, "storage": "demo",
                "storage_message": "", "database_encrypted": False, "documents_encrypted": False}
    found = storage()
    return {"mode": "protected", "setup_required": found["state"] == "setup",
            "recovery_available": found["state"] == "locked", "storage": found["state"],
            "storage_message": found["message"], "database_encrypted": found["state"] == "locked",
            "documents_encrypted": False}


def _locked_vault() -> dict:
    found = storage()
    if found["state"] == "setup":
        raise AuthError(409, "Set up the local owner first")
    if found["state"] != "locked":
        raise AuthError(409, found["message"])
    return found["vault"]


# --- attempt limiting ------------------------------------------------------------------------------

def _blocked() -> int:
    remaining = _blocked_until - time.monotonic()
    return math.ceil(remaining) if remaining > 0 else 0


def _check_not_blocked() -> None:
    with _lock:
        wait = _blocked()
    if wait:
        raise AuthError(429, f"Too many incorrect attempts. Try again in {wait} seconds.", retry_after=wait)


def _failed(detail: str, status: int = 401) -> AuthError:
    global _failures, _blocked_until, _failed_while_locked
    with _lock:
        _failures += 1
        if status == 401:
            _failed_while_locked += 1
        if _failures >= MAX_FAILURES:
            _blocked_until = time.monotonic() + min(LOCKOUT_BASE_SECONDS * 2 ** (_failures - MAX_FAILURES), LOCKOUT_MAX_SECONDS)
    return AuthError(status, detail)


def _succeeded() -> None:
    global _failures, _blocked_until
    with _lock:
        _failures, _blocked_until = 0, 0.0


def take_failed_attempts() -> int:
    """Failed unlock/recovery attempts since the last successful unlock, for the unlock audit entry."""
    global _failed_while_locked
    with _lock:
        count, _failed_while_locked = _failed_while_locked, 0
    return count


# --- vault and database -------------------------------------------------------------------------------

def _write(doc: dict, opened: vault.Opened, *, new: bool = False) -> None:
    try:
        vault.write(vault_path(), doc, opened.dek, replace_existing=not new)
    except vault.VaultError as exc:
        raise vault_error(exc) from None
    except OSError:
        raise AuthError(503, "The key vault could not be written; the previous vault was kept") from None
    opened.doc = doc


def _open_database(opened: vault.Opened, events: list) -> tuple[dict, bytearray]:
    """Derives the database key from the authenticated DEK, opens (or, for a just-created installation, creates) the
    encrypted database, applies schema migrations, and checks that the database belongs to this vault."""
    doc = opened.doc
    key = bytearray(vault.derive(opened.dek, "database"))
    try:
        if not hmac.compare_digest(vault.fingerprint(key), doc["database"]["key_fingerprint"]):
            raise AuthError(409, "The vault's database key record does not match its data key. Nothing was changed.")
        pending = doc["database"]["state"] == "pending"
        path = database_path()
        create = pending and file_kind(path) in ("missing", "empty")
        try:
            conn = open_encrypted(path, key, create=create)
        except DatabaseRefused as exc:
            raise AuthError(409, exc.message) from None
        try:
            conn.isolation_level = None   # migrations manage their own transactions
            migrate(conn)
            conn.isolation_level = ""
            row = owner(conn)
            if row is None and pending:   # setup: the owner row binds this database to its vault
                conn.execute("INSERT INTO local_owner(id, uid, display_name, created_at, vault_id, vault_key_id) "
                             "VALUES(1,?,?,?,?,?)", (doc["owner_uid"], doc["owner_display_name"], now_utc(),
                                                     doc["vault_id"], doc["key_id"]))
                conn.commit()
                row = owner(conn)
                events.append(("database_created", {"cipher": "sqlcipher4", "sqlcipher_version": SQLCIPHER_VERSION,
                                                    "key_fingerprint": doc["database"]["key_fingerprint"]}))
            if row is None or (row["uid"], row["vault_id"], row["vault_key_id"]) != (doc["owner_uid"], doc["vault_id"], doc["key_id"]):
                raise AuthError(409, "The encrypted database does not belong to this key vault. Nothing was changed.")
        except sqlite.DatabaseError:   # a page failed SQLCipher's HMAC check, or the file is truncated
            raise AuthError(409, "The encrypted database is damaged (a page failed its integrity check). Restore it "
                                 "from a backup; nothing was changed.") from None
        finally:
            conn.close()
        if pending:
            _write(vault.with_database_state(opened, "encrypted", now_utc()), opened)
        return {k: row[k] for k in ("uid", "display_name")}, key
    except BaseException:
        _wipe(key)
        raise


def _ensure_recovery_shown(opened: vault.Opened, result: Unlocked) -> None:
    """If the owner never confirmed saving a recovery key (or a write interrupted it), issue a fresh one to show now."""
    if result.recovery_key is None and not opened.doc["recovery_confirmed"]:
        doc, result.recovery_key = vault.with_new_recovery_key(opened, now_utc())
        _write(doc, opened)
        result.events.append(("recovery_key_issued", {"vault_id": doc["vault_id"], "reason": "not yet confirmed"}))


def _unlocked(opened: vault.Opened, events: list, recovery_key: str | None = None) -> Unlocked:
    try:
        row, key = _open_database(opened, events)
        result = Unlocked(row, opened, key, recovery_key, events)
        _ensure_recovery_shown(opened, result)
        return result
    except BaseException:
        opened.wipe()
        raise


# --- operations --------------------------------------------------------------------------------------

def setup(display_name: str, passphrase: str) -> Unlocked:
    if app_mode() != "protected":
        raise AuthError(409, "Demonstration mode has no owner; start OncoScope in protected mode to set one up")
    check_passphrase_policy(passphrase)
    with _vault_lock:
        found = storage()
        if found["state"] != "setup":
            raise AuthError(409, "This installation already has a local owner" if found["state"] == "locked"
                            else found["message"])
        for leftover in vault.stale_temp_files(vault_path()):
            leftover.unlink(missing_ok=True)
        opened, recovery_key = vault.create(str(uuid.uuid4()), passphrase, now_utc(), display_name=display_name)
        _write(opened.doc, opened, new=True)   # vault first ("pending"), then the database, then "encrypted"
        events: list = [
            ("owner_setup", {"owner_uid": opened.doc["owner_uid"], "display_name": display_name}),
            ("vault_initialized", {"vault_id": opened.doc["vault_id"], "key_id": opened.doc["key_id"], "kdf": "argon2id",
                                   "wrap": "aes-256-gcm", "vault_version": opened.doc["version"]}),
            ("recovery_key_issued", {"vault_id": opened.doc["vault_id"]}),
        ]
        return _unlocked(opened, events, recovery_key)


def unlock(passphrase: str) -> Unlocked:
    _check_not_blocked()
    with _vault_lock:
        doc = _locked_vault()
        try:
            opened = vault.open_with_passphrase(doc, passphrase)
        except vault.WrongCredential:
            raise _failed("Incorrect passphrase") from None
        except vault.VaultError as exc:
            raise vault_error(exc) from None
        _succeeded()
        return _unlocked(opened, [])


def recover(recovery_text: str, new_passphrase: str) -> Unlocked:
    """Opens the vault with the recovery key and sets a new passphrase. The DEK and the recovery key stay the same."""
    _check_not_blocked()
    check_passphrase_policy(new_passphrase)
    with _vault_lock:
        doc = _locked_vault()
        try:
            opened = vault.open_with_recovery_key(doc, recovery_text)
        except vault.WrongCredential as exc:
            if str(exc) in ("format", "checksum"):
                raise AuthError(422, "That is not a valid recovery key. Check for a typing mistake: it has 14 groups of "
                                     "4 letters and digits.") from None
            raise _failed("Incorrect recovery key") from None
        except vault.VaultError as exc:
            raise vault_error(exc) from None
        _succeeded()
        events = [("recovered", {"vault_id": doc["vault_id"], "method": "recovery_key"}),
                  ("passphrase_changed", {"vault_id": doc["vault_id"], "method": "recovery_key"})]
        result = _unlocked(opened, events)   # the database must open before the passphrase is changed
        try:
            _write(vault.with_passphrase(opened, new_passphrase, now_utc()), opened)
        except BaseException:
            opened.wipe()
            _wipe(result.db_key)
            raise
        return result


def _vault_on_disk(session: Session) -> dict:
    try:
        doc = vault.read(vault_path())
    except vault.VaultError as exc:
        raise vault_error(exc) from None
    if (doc["vault_id"], doc["key_id"]) != (session.keys.doc["vault_id"], session.keys.doc["key_id"]):
        raise AuthError(409, "The key vault on disk is not the one this session opened (it was replaced). Nothing was changed.")
    return doc


def change_passphrase(session: Session, current: str, new: str) -> list[tuple[str, dict]]:
    if session.keys is None:
        raise AuthError(409, "This session holds no vault key; lock and unlock again")
    _check_not_blocked()
    check_passphrase_policy(new)
    if hmac.compare_digest(current.encode(), new.encode()):
        raise AuthError(422, "Choose a passphrase different from the current one")
    with _vault_lock:
        doc = _vault_on_disk(session)
        try:
            check = vault.open_with_passphrase(doc, current)
        except vault.WrongCredential:
            # 403, not 401: the session itself is valid, and the UI treats 401 as "session ended".
            raise _failed("The current passphrase is incorrect", status=403) from None
        except vault.VaultError as exc:
            raise vault_error(exc) from None
        same = hmac.compare_digest(bytes(check.dek), bytes(session.keys.dek))
        check.wipe()
        if not same:
            raise AuthError(409, "The vault on disk no longer matches this session; lock and unlock again")
        _succeeded()
        # Re-wraps the same DEK: the database key, and so the encrypted database, are unchanged.
        _write(vault.with_passphrase(session.keys, new, now_utc()), session.keys)
    return [("passphrase_changed", {"vault_id": doc["vault_id"], "method": "passphrase"})]


def confirm_recovery_key(session: Session) -> list[tuple[str, dict]]:
    if session.keys is None:
        raise AuthError(409, "This session holds no vault key; lock and unlock again")
    with _vault_lock:
        if session.keys.doc["recovery_confirmed"]:
            return []
        _write(vault.with_recovery_confirmed(session.keys, now_utc()), session.keys)
    return [("recovery_key_confirmed", {"vault_id": session.keys.doc["vault_id"]})]


def vault_status(session: Session) -> dict:
    """Public facts about the unlocked vault and database. Keys are shown only as fingerprints, never themselves."""
    keys = session.keys
    if keys is None:
        raise AuthError(409, "This session holds no vault key; lock and unlock again")
    doc, pw = keys.doc, keys.doc["wraps"]["passphrase"]
    database = doc["database"]
    return {"initialized": True, "format": doc["format"], "version": doc["version"], "vault_id": doc["vault_id"],
            "key_id": doc["key_id"], "created_at": doc["created_at"], "updated_at": doc["updated_at"],
            "recovery_confirmed": doc["recovery_confirmed"],
            "passphrase_kdf": {k: pw[k] for k in ("kdf", "time_cost", "memory_cost", "parallelism")},
            "derived_key_fingerprints": {p: vault.fingerprint(vault.derive(keys.dek, p)) for p in vault.PURPOSES},
            "database": {"state": database["state"], "cipher": "SQLCipher 4 (AES-256-CBC, HMAC-SHA512 per page)",
                         "sqlcipher_version": SQLCIPHER_VERSION, "key_fingerprint": database["key_fingerprint"],
                         "migrated_from_plaintext_at": database.get("migrated_from_plaintext_at")},
            "encrypted_at_rest": {"database": database["state"] == "encrypted", "documents": False, "backups":
                                  "database snapshot encrypted; documents and manifest are not"}}


# --- sessions ---------------------------------------------------------------------------------------

def start_session(owner_row: dict, keys: vault.Opened | None = None, db_key: bytearray | None = None) -> str:
    """One active session per installation: unlocking again ends any earlier session (and forgets its keys)."""
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        for s in _sessions.values():
            if s.keys is not keys:
                _drop(s)
        _sessions.clear()
        _sessions[_key(token)] = Session(actor=actor_of(owner_row), created=now, last_seen=now, keys=keys, db_key=db_key)
    return token


def _expired(session: Session, now: float) -> bool:
    return now - session.last_seen > idle_seconds() or now - session.created > max_seconds()


def session_for(token: str | None) -> Session | None:
    """The live session for a token, refreshing its idle timer; None for missing, unknown or expired tokens."""
    if not token:
        return None
    now = time.monotonic()
    with _lock:
        key = _key(token)
        session = _sessions.get(key)
        if session is None:
            return None
        if _expired(session, now):
            _drop(session)
            del _sessions[key]
            return None
        session.last_seen = now
        return session


def sweep() -> int:
    """Ends expired sessions (wiping their keys) without waiting for their next request. Run periodically."""
    now = time.monotonic()
    with _lock:
        expired = [k for k, s in _sessions.items() if _expired(s, now)]
        for k in expired:
            _drop(_sessions.pop(k))
    return len(expired)


def end_session(token: str | None) -> bool:
    if not token:
        return False
    with _lock:
        session = _sessions.pop(_key(token), None)
        if session is not None:
            _drop(session)
        return session is not None


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None
