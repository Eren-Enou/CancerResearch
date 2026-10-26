"""Protected local access: one local owner, an Argon2id passphrase verifier, short-lived in-memory sessions.

Scope (M3.1-A): this controls who can use OncoScope's HTTP API. It does NOT encrypt anything at rest; anyone who can
read the database file can still read the records (M3.1-B/C). Hashing is done by argon2-cffi; tokens come from the
`secrets` module. No cryptography is implemented here.
"""
from __future__ import annotations

import hashlib
import math
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .db import app_mode, now_utc

# argon2-cffi's default parameters are its RFC 9106 low-memory profile (Argon2id, t=3, m=64 MiB, p=4, 16-byte salt).
_hasher = PasswordHasher()

PASSPHRASE_MIN, PASSPHRASE_MAX = 12, 1024
MAX_FAILURES = 5                  # wrong passphrases allowed before unlocking is refused for a while
LOCKOUT_BASE_SECONDS = 30         # doubles with each further failure ...
LOCKOUT_MAX_SECONDS = 15 * 60     # ... up to 15 minutes


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


class AuthError(Exception):
    def __init__(self, status: int, detail: str, retry_after: int | None = None):
        super().__init__(detail)
        self.status, self.detail, self.retry_after = status, detail, retry_after


_lock = threading.Lock()
# Keyed by SHA-256 of the token, so the table itself never holds a usable token. Lives only in this process's memory:
# restarting the server ends every session.
_sessions: dict[str, Session] = {}
_failures = 0
_blocked_until = 0.0


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def reset() -> None:
    """Forget all sessions and failure counts (server start, and tests)."""
    global _failures, _blocked_until
    with _lock:
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


def create_owner(conn, display_name: str, passphrase: str) -> dict:
    if app_mode() != "protected":
        raise AuthError(409, "Demonstration mode has no owner; start OncoScope in protected mode to set one up")
    if owner(conn):
        raise AuthError(409, "This installation already has a local owner")
    check_passphrase_policy(passphrase)
    conn.execute("INSERT INTO local_owner(id, uid, display_name, password_verifier, created_at) VALUES(1,?,?,?,?)",
                 (str(uuid.uuid4()), display_name, _hasher.hash(passphrase), now_utc()))
    return owner(conn)


def _blocked() -> int:
    remaining = _blocked_until - time.monotonic()
    return math.ceil(remaining) if remaining > 0 else 0


def verify_passphrase(conn, passphrase: str) -> dict:
    """Checks the passphrase against the stored verifier, with a growing lockout after repeated failures."""
    global _failures, _blocked_until
    with _lock:
        wait = _blocked()
        if wait:
            raise AuthError(429, f"Too many incorrect passphrases. Try again in {wait} seconds.", retry_after=wait)
    row = owner(conn)
    if row is None:
        raise AuthError(409, "Set up the local owner first")
    try:
        _hasher.verify(row["password_verifier"], passphrase)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        with _lock:
            _failures += 1
            if _failures >= MAX_FAILURES:
                lockout = min(LOCKOUT_BASE_SECONDS * 2 ** (_failures - MAX_FAILURES), LOCKOUT_MAX_SECONDS)
                _blocked_until = time.monotonic() + lockout
        raise AuthError(401, "Incorrect passphrase") from None
    with _lock:
        _failures, _blocked_until = 0, 0.0
    if _hasher.check_needs_rehash(row["password_verifier"]):
        conn.execute("UPDATE local_owner SET password_verifier=?, updated_at=? WHERE id=1", (_hasher.hash(passphrase), now_utc()))
    return row


def start_session(owner_row: dict) -> str:
    """One active session per installation: unlocking again ends any earlier session."""
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        _sessions.clear()
        _sessions[_key(token)] = Session(actor=actor_of(owner_row), created=now, last_seen=now)
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
            del _sessions[key]
            return None
        session.last_seen = now
        return session


def end_session(token: str | None) -> bool:
    if not token:
        return False
    with _lock:
        return _sessions.pop(_key(token), None) is not None


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None
