"""Protected local access: one local owner, a key vault opened by the owner's passphrase or recovery key, and
short-lived in-memory sessions.

Scope (M3.1-A/B): this controls who can use OncoScope's HTTP API and keeps the data-encryption key (DEK) recoverable.
It does NOT encrypt anything at rest yet; anyone who can read the database file can still read the records (M3.1-C/D).
Argon2id comes from argon2-cffi, AES-GCM/HKDF from pyca/cryptography (see vault.py), tokens from `secrets`.

Order of truth: once a vault exists, the vault's passphrase wrap decides whether a passphrase is correct. The Argon2id
verifier in `local_owner` (M3.1-A) is kept in step and is re-synchronised if an interrupted passphrase change left it
behind. Before a vault exists, the verifier decides, and the first successful unlock creates the vault.
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

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import vault
from .db import app_mode, now_utc, vault_path

# argon2-cffi's default parameters are its RFC 9106 low-memory profile (Argon2id, t=3, m=64 MiB, p=4, 16-byte salt).
_hasher = PasswordHasher()

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


@dataclass
class Session:
    actor: tuple[str, str]
    created: float
    last_seen: float
    keys: vault.Opened | None = None   # the unlocked vault; wiped when the session ends


@dataclass
class Unlocked:
    """Result of setup, unlock or recovery: who, the opened vault, a recovery key to show once, and audit events."""
    owner: dict
    opened: vault.Opened
    recovery_key: str | None = None
    events: list[tuple[str, dict]] = field(default_factory=list)


class AuthError(Exception):
    def __init__(self, status: int, detail: str, retry_after: int | None = None):
        super().__init__(detail)
        self.status, self.detail, self.retry_after = status, detail, retry_after


_lock = threading.Lock()
_vault_lock = threading.Lock()   # one vault change at a time
# Keyed by SHA-256 of the token, so the table itself never holds a usable token. Lives only in this process's memory:
# restarting the server ends every session and forgets every key.
_sessions: dict[str, Session] = {}
_failures = 0
_blocked_until = 0.0


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _drop(session: Session) -> None:
    if session.keys is not None:
        session.keys.wipe()
        session.keys = None


def reset() -> None:
    """Forget all sessions, keys and failure counts (server start, and tests)."""
    global _failures, _blocked_until
    with _lock:
        for s in _sessions.values():
            _drop(s)
        _sessions.clear()
        _failures, _blocked_until = 0, 0.0


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
    global _failures, _blocked_until
    with _lock:
        _failures += 1
        if _failures >= MAX_FAILURES:
            _blocked_until = time.monotonic() + min(LOCKOUT_BASE_SECONDS * 2 ** (_failures - MAX_FAILURES), LOCKOUT_MAX_SECONDS)
    return AuthError(status, detail)


def _succeeded() -> None:
    global _failures, _blocked_until
    with _lock:
        _failures, _blocked_until = 0, 0.0


def _verifier_matches(row: dict, passphrase: str) -> bool:
    try:
        return _hasher.verify(row["password_verifier"], passphrase)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _store_verifier(conn, passphrase: str) -> None:
    conn.execute("UPDATE local_owner SET password_verifier=?, updated_at=? WHERE id=1", (_hasher.hash(passphrase), now_utc()))


# --- vault binding ---------------------------------------------------------------------------------

def _bound_vault(row: dict) -> dict:
    """The vault this installation recorded. Missing or foreign vaults are refused; a new DEK is never made here."""
    try:
        doc = vault.read(vault_path())
    except vault.VaultError as exc:
        if exc.code == "missing":
            raise AuthError(409, "The key vault file is missing. Restore it from a backup; OncoScope will not create a "
                                 "new key for an installation that already has one.") from None
        raise vault_error(exc) from None
    if (doc["vault_id"], doc["key_id"], doc["owner_uid"]) != (row["vault_id"], row["vault_key_id"], row["uid"]):
        raise AuthError(409, "The key vault does not belong to this installation (it was replaced). Nothing was changed.")
    return doc


def _bind(conn, opened: vault.Opened) -> None:
    conn.execute("UPDATE local_owner SET vault_id=?, vault_key_id=?, updated_at=? WHERE id=1",
                 (opened.doc["vault_id"], opened.doc["key_id"], now_utc()))


def _write(doc: dict, opened: vault.Opened, *, new: bool = False) -> None:
    try:
        vault.write(vault_path(), doc, opened.dek, replace_existing=not new)
    except vault.VaultError as exc:
        raise vault_error(exc) from None
    except OSError:
        raise AuthError(503, "The key vault could not be written; the previous vault was kept") from None
    opened.doc = doc


def _initialise(conn, row: dict, passphrase: str, result_events: list) -> tuple[vault.Opened, str | None]:
    """First vault for an owner that has none recorded (new setup, or an M3.1-A installation). The passphrase has been
    verified. A vault file left by an interrupted earlier initialisation is adopted if it opens, never overwritten."""
    path = vault_path()
    for leftover in vault.stale_temp_files(path):
        leftover.unlink(missing_ok=True)
    if path.exists():
        try:
            doc = vault.read(path)
            if doc["owner_uid"] != row["uid"]:
                raise AuthError(409, "A key vault for a different owner is in the data folder. Move it away; OncoScope "
                                     "never replaces a key vault automatically.")
            opened = vault.open_with_passphrase(doc, passphrase)
        except vault.VaultError as exc:
            raise vault_error(exc) from None
        except vault.WrongCredential:
            raise AuthError(409, "A key vault is in the data folder but does not open with this passphrase. Move it away "
                                 "or use its recovery key; nothing was changed.") from None
        _bind(conn, opened)
        result_events.append(("vault_adopted", {"vault_id": doc["vault_id"], "key_id": doc["key_id"]}))
        return opened, None
    opened, recovery_key = vault.create(row["uid"], passphrase, now_utc())
    _write(opened.doc, opened, new=True)
    _bind(conn, opened)
    result_events.append(("vault_initialized", {"vault_id": opened.doc["vault_id"], "key_id": opened.doc["key_id"],
                                                 "kdf": "argon2id", "wrap": "aes-256-gcm"}))
    result_events.append(("recovery_key_issued", {"vault_id": opened.doc["vault_id"]}))
    return opened, recovery_key


def _ensure_recovery_shown(opened: vault.Opened, result: Unlocked) -> None:
    """If the owner never confirmed saving a recovery key (or a write interrupted it), issue a fresh one to show now."""
    if result.recovery_key is None and not opened.doc["recovery_confirmed"]:
        doc, result.recovery_key = vault.with_new_recovery_key(opened, now_utc())
        _write(doc, opened)
        result.events.append(("recovery_key_issued", {"vault_id": doc["vault_id"], "reason": "not yet confirmed"}))


# --- operations --------------------------------------------------------------------------------------

def setup(conn, display_name: str, passphrase: str) -> Unlocked:
    if app_mode() != "protected":
        raise AuthError(409, "Demonstration mode has no owner; start OncoScope in protected mode to set one up")
    if owner(conn):
        raise AuthError(409, "This installation already has a local owner")
    check_passphrase_policy(passphrase)
    if vault_path().exists():
        raise AuthError(409, "A key vault already exists in the data folder. Move it away to set up a new installation; "
                             "OncoScope never replaces a key vault automatically.")
    conn.execute("INSERT INTO local_owner(id, uid, display_name, password_verifier, created_at) VALUES(1,?,?,?,?)",
                 (str(uuid.uuid4()), display_name, _hasher.hash(passphrase), now_utc()))
    row = owner(conn)
    events: list = [("owner_setup", {"owner_uid": row["uid"], "display_name": row["display_name"], "verifier": "argon2id"})]
    opened, recovery_key = _initialise(conn, row, passphrase, events)
    return Unlocked(owner(conn), opened, recovery_key, events)


def unlock(conn, passphrase: str) -> Unlocked:
    _check_not_blocked()
    row = owner(conn)
    if row is None:
        raise AuthError(409, "Set up the local owner first")
    with _vault_lock:
        events: list = []
        recovery_key = None
        if row["vault_id"] is None:
            if not _verifier_matches(row, passphrase):
                raise _failed("Incorrect passphrase")
            opened, recovery_key = _initialise(conn, row, passphrase, events)
        else:
            doc = _bound_vault(row)
            try:
                opened = vault.open_with_passphrase(doc, passphrase)
            except vault.WrongCredential:
                raise _failed("Incorrect passphrase") from None
            except vault.VaultError as exc:
                raise vault_error(exc) from None
            if not _verifier_matches(row, passphrase):   # an interrupted passphrase change: the vault is authoritative
                _store_verifier(conn, passphrase)
                events.append(("verifier_resynced", {"vault_id": doc["vault_id"]}))
        _succeeded()
        if _hasher.check_needs_rehash(row["password_verifier"]):
            _store_verifier(conn, passphrase)
        result = Unlocked(owner(conn), opened, recovery_key, events)
        _ensure_recovery_shown(opened, result)
        return result


def recover(conn, recovery_text: str, new_passphrase: str) -> Unlocked:
    """Opens the vault with the recovery key and sets a new passphrase. The DEK and the recovery key stay the same."""
    _check_not_blocked()
    row = owner(conn)
    if row is None:
        raise AuthError(409, "Set up the local owner first")
    if row["vault_id"] is None:
        raise AuthError(409, "This installation has no key vault yet, so there is nothing to recover. Unlock with the passphrase.")
    check_passphrase_policy(new_passphrase)
    with _vault_lock:
        doc = _bound_vault(row)
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
        _write(vault.with_passphrase(opened, new_passphrase, now_utc()), opened)
        _store_verifier(conn, new_passphrase)
        events = [("recovered", {"vault_id": doc["vault_id"], "method": "recovery_key"}),
                  ("passphrase_changed", {"vault_id": doc["vault_id"], "method": "recovery_key"})]
        result = Unlocked(owner(conn), opened, None, events)
        _ensure_recovery_shown(opened, result)
        return result


def change_passphrase(conn, session: Session, current: str, new: str) -> list[tuple[str, dict]]:
    if session.keys is None:
        raise AuthError(409, "This session holds no vault key; lock and unlock again")
    _check_not_blocked()
    check_passphrase_policy(new)
    if hmac.compare_digest(current.encode(), new.encode()):
        raise AuthError(422, "Choose a passphrase different from the current one")
    row = owner(conn)
    with _vault_lock:
        doc = _bound_vault(row)
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
        _write(vault.with_passphrase(session.keys, new, now_utc()), session.keys)   # vault first: it is authoritative
        _store_verifier(conn, new)
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
    """Public facts about the unlocked vault. Derived keys are shown only as fingerprints, to compare them over time."""
    keys = session.keys
    if keys is None:
        raise AuthError(409, "This session holds no vault key; lock and unlock again")
    doc, pw = keys.doc, keys.doc["wraps"]["passphrase"]
    return {"initialized": True, "format": doc["format"], "version": doc["version"], "vault_id": doc["vault_id"],
            "key_id": doc["key_id"], "created_at": doc["created_at"], "updated_at": doc["updated_at"],
            "recovery_confirmed": doc["recovery_confirmed"],
            "passphrase_kdf": {k: pw[k] for k in ("kdf", "time_cost", "memory_cost", "parallelism")},
            "derived_key_fingerprints": {p: vault.fingerprint(vault.derive(keys.dek, p)) for p in vault.PURPOSES},
            "encrypted_at_rest": False}


# --- sessions ---------------------------------------------------------------------------------------

def start_session(owner_row: dict, keys: vault.Opened | None = None) -> str:
    """One active session per installation: unlocking again ends any earlier session (and forgets its key)."""
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        for s in _sessions.values():
            if s.keys is not keys:
                _drop(s)
        _sessions.clear()
        _sessions[_key(token)] = Session(actor=actor_of(owner_row), created=now, last_seen=now, keys=keys)
    return token


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
        if now - session.last_seen > idle_seconds() or now - session.created > max_seconds():
            _drop(session)
            del _sessions[key]
            return None
        session.last_seen = now
        return session


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
