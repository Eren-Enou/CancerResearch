"""Local key vault (M3.1-B/C): one random data-encryption key (DEK), wrapped twice — by the owner's passphrase and by a
recovery key. Since M3.1-C the SQLCipher database key is derived from it; documents follow in M3.1-D.

All primitives come from maintained libraries: Argon2id from argon2-cffi; AES-256-GCM, HKDF-SHA256 and HMAC-SHA256 from
pyca/cryptography; randomness from `secrets`. This module only arranges them.

Vault file (JSON, format "oncoscope-key-vault", version 2; version 1 is still read and upgraded):
- header: vault id, owner uid, DEK key id (an HKDF-derived identifier, not the key), timestamps, recovery confirmation;
- v2 only: `owner_display_name` and `database` (state "plaintext" | "pending" | "encrypted", cipher, and the database
  key's fingerprint). This is what OncoScope needs before unlock, outside the encrypted database. The MAC covers it, so
  it is authenticated as soon as the vault opens, before any SQLCipher call;
- `wraps.passphrase`: Argon2id parameters + salt, AES-GCM nonce + ciphertext of the DEK;
- `wraps.recovery`: HKDF salt, AES-GCM nonce + ciphertext of the DEK;
- `mac`: HMAC-SHA256 over the whole body under a DEK-derived key (detects edits made by someone without the DEK);
- `checksum`: SHA-256 over everything else (tells accidental damage apart from a wrong passphrase).
Each wrap's associated data binds it to the format, version, vault id, key id and wrap kind, so wraps cannot be moved
between vaults or kinds. A v2 wrap records the version it was bound under (`aad_version`), so wraps carried over from a
v1 vault keep working (the recovery wrap cannot be re-made without the recovery key). The DEK, passphrase and recovery
key are never written anywhere.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMAT, VERSION = "oncoscope-key-vault", 2
SUPPORTED_VERSIONS = (1, 2)
# Where the database stands. v1 vaults predate encryption, so their database is plaintext.
DATABASE_STATES = ("plaintext", "pending", "encrypted")
DATABASE_CIPHER = "sqlcipher4-raw-key"
KEY_BYTES, SALT_BYTES, NONCE_BYTES = 32, 16, 12
# Argon2id, RFC 9106 "second recommended" (low-memory) profile; stored per vault so it can be raised later.
ARGON2 = {"time_cost": 3, "memory_cost": 65536, "parallelism": 4}
ARGON2_LIMITS = {"time_cost": (1, 20), "memory_cost": (19456, 4 * 1024 * 1024), "parallelism": (1, 16)}

# Domain-separated HKDF labels. Purposes are a closed list: an unknown purpose is an error, never a new key.
PURPOSES = {
    "database": b"oncoscope/v1/sqlcipher-database",      # M3.1-C
    "documents": b"oncoscope/v1/document-encryption",    # M3.1-D
    "document-names": b"oncoscope/v1/document-names",    # M3.1-D (HMAC file names)
    "backup-manifest": b"oncoscope/v1/backup-manifest",  # M3.1-D
    "audit": b"oncoscope/v1/audit-mac",                  # M3.1-E
}
_KEY_ID = b"oncoscope/vault/v1/key-id"
_VAULT_MAC = b"oncoscope/vault/v1/vault-mac"
_RECOVERY_KEK = b"oncoscope/vault/v1/recovery-kek"

# Recovery keys: 32 random bytes in RFC 4648 base32 (52 characters) plus a 4-character checksum, in groups of four.
_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_LOOKALIKES = str.maketrans({"0": "O", "1": "I", "8": "B"})


class VaultError(Exception):
    """A vault problem that is not a wrong credential: missing, damaged, unsupported or not this installation's."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class WrongCredential(Exception):
    """The passphrase or recovery key does not open this vault (or the wrap was altered: indistinguishable)."""


@dataclass
class Opened:
    """An unlocked vault: the parsed document and the DEK, kept in a bytearray so it can be overwritten on lock."""
    doc: dict
    dek: bytearray

    def wipe(self) -> None:
        for i in range(len(self.dek)):
            self.dek[i] = 0


# --- encoding helpers ---------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value, length: int | None, field: str) -> bytes:
    try:
        data = base64.b64decode(value, validate=True) if isinstance(value, str) else None
    except (binascii.Error, ValueError):
        data = None
    if data is None or (length is not None and len(data) != length):
        raise VaultError("damaged", f"The key vault is damaged ({field} is malformed)")
    return data


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _hkdf(ikm: bytes, info: bytes, salt: bytes | None = None, length: int = KEY_BYTES) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def format_recovery_key(raw: bytes) -> str:
    body = base64.b32encode(raw).decode("ascii").rstrip("=")
    check = base64.b32encode(hashlib.sha256(b"oncoscope-recovery-check" + raw).digest()).decode("ascii")[:4]
    text = body + check
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def parse_recovery_key(text: str) -> bytes:
    """Accepts any case, spaces or hyphens, and 0/1/8 typed for O/I/B. A typo is reported as such, not as a wrong key."""
    cleaned = "".join(ch for ch in str(text).upper() if ch not in " -\t\r\n").translate(_LOOKALIKES)
    if len(cleaned) != 56 or any(ch not in _B32 for ch in cleaned):
        raise WrongCredential("format")
    raw = base64.b32decode(cleaned[:52] + "====")
    if format_recovery_key(raw).replace("-", "") != cleaned:
        raise WrongCredential("checksum")
    return raw


# --- key derivation -----------------------------------------------------------------------------

def _passphrase_kek(passphrase: str, wrap: dict) -> bytes:
    return hash_secret_raw(passphrase.encode("utf-8"), _unb64(wrap["salt"], SALT_BYTES, "passphrase salt"),
                           time_cost=wrap["time_cost"], memory_cost=wrap["memory_cost"], parallelism=wrap["parallelism"],
                           hash_len=KEY_BYTES, type=Type.ID)


def _recovery_kek(recovery: bytes, wrap: dict) -> bytes:
    return _hkdf(recovery, _RECOVERY_KEK, salt=_unb64(wrap["salt"], SALT_BYTES, "recovery salt"))


def key_id(dek: bytes) -> str:
    """A stable, public identifier of a DEK (HKDF output, so it reveals nothing about the key)."""
    return _hkdf(bytes(dek), _KEY_ID, length=16).hex()


def derive(dek: bytes, purpose: str) -> bytes:
    """A purpose-specific 256-bit key. The same DEK always yields the same key; different purposes never collide."""
    if purpose not in PURPOSES:
        raise ValueError(f"Unknown key purpose {purpose!r}")
    return _hkdf(bytes(dek), PURPOSES[purpose])


def fingerprint(key: bytes) -> str:
    """A short public identifier of a derived key, for comparing keys without revealing them."""
    return hmac.new(bytes(key), b"oncoscope/v1/fingerprint", hashlib.sha256).hexdigest()[:16]


# --- building and checking vault documents --------------------------------------------------------

def _aad(doc: dict, kind: str, version: int) -> bytes:
    return _canonical({"format": doc["format"], "version": version, "vault_id": doc["vault_id"],
                       "key_id": doc["key_id"], "wrap": kind})


def _wrap(doc: dict, kind: str, kek: bytes, dek: bytes) -> dict:
    nonce = secrets.token_bytes(NONCE_BYTES)
    wrap = {"nonce": _b64(nonce),
            "ciphertext": _b64(AESGCM(kek).encrypt(nonce, bytes(dek), _aad(doc, kind, doc["version"])))}
    return wrap if doc["version"] == 1 else {**wrap, "aad_version": doc["version"]}


def _unwrap(doc: dict, kind: str, kek: bytes) -> bytearray:
    wrap = doc["wraps"][kind]
    nonce = _unb64(wrap["nonce"], NONCE_BYTES, f"{kind} nonce")
    ciphertext = _unb64(wrap["ciphertext"], KEY_BYTES + 16, f"{kind} ciphertext")
    try:
        return bytearray(AESGCM(kek).decrypt(nonce, ciphertext, _aad(doc, kind, wrap.get("aad_version", 1))))
    except InvalidTag:
        raise WrongCredential(kind) from None


def _body(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in ("mac", "checksum")}


def _seal(doc: dict, dek: bytes) -> dict:
    """Adds the DEK-keyed MAC and the plain checksum. Call after every change."""
    body = _body(doc)
    mac = hmac.new(_hkdf(bytes(dek), _VAULT_MAC), _canonical(body), hashlib.sha256).hexdigest()
    sealed = {**body, "mac": mac}
    sealed["checksum"] = hashlib.sha256(_canonical(sealed)).hexdigest()
    return sealed


def _check_mac(doc: dict, dek: bytes) -> None:
    expected = hmac.new(_hkdf(bytes(dek), _VAULT_MAC), _canonical(_body(doc)), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, doc["mac"]) or key_id(dek) != doc["key_id"]:
        raise VaultError("tampered", "The key vault was altered: its integrity code does not match")


def _database(dek: bytes, state: str, now: str, migrated_at: str | None = None) -> dict:
    return {"state": state, "cipher": DATABASE_CIPHER, "key_fingerprint": fingerprint(derive(dek, "database")),
            "updated_at": now, "migrated_from_plaintext_at": migrated_at}


def create(owner_uid: str, passphrase: str, now: str, *, display_name: str, database_state: str = "pending",
           version: int = VERSION) -> tuple[Opened, str]:
    """A new vault with a fresh DEK. Returns the opened vault and the formatted recovery key (shown once, never stored).
    `version=1` exists only so tests can build the vaults M3.1-B wrote."""
    dek = bytearray(secrets.token_bytes(KEY_BYTES))
    recovery = secrets.token_bytes(KEY_BYTES)
    doc = {"format": FORMAT, "version": version, "vault_id": str(uuid.uuid4()), "owner_uid": owner_uid,
           "key_id": key_id(dek), "created_at": now, "updated_at": now, "recovery_confirmed": False, "wraps": {}}
    if version >= 2:
        doc |= {"owner_display_name": display_name, "database": _database(dek, database_state, now)}
    doc["wraps"]["passphrase"] = _new_passphrase_wrap(doc, passphrase, dek)
    doc["wraps"]["recovery"] = _new_recovery_wrap(doc, recovery, dek)
    return Opened(_seal(doc, dek), dek), format_recovery_key(recovery)


def database_state(doc: dict) -> str:
    """Readable before unlock (it is not secret); authenticated by the MAC once the vault opens."""
    return doc["database"]["state"] if doc["version"] >= 2 else "plaintext"


def upgraded(opened: Opened, display_name: str, now: str) -> dict:
    """A v1 vault as v2: same DEK, ids and wraps (which keep their v1 binding); the database is still plaintext."""
    if opened.doc["version"] != 1:
        return opened.doc
    body = _body(opened.doc)
    wraps = {kind: {**wrap, "aad_version": 1} for kind, wrap in body["wraps"].items()}
    doc = {**body, "version": 2, "updated_at": now, "owner_display_name": display_name, "wraps": wraps,
           "database": _database(opened.dek, "plaintext", now)}
    return _seal(doc, opened.dek)


def with_database_state(opened: Opened, state: str, now: str, *, migrated: bool = False) -> dict:
    if opened.doc["version"] < 2 or state not in DATABASE_STATES:
        raise ValueError("A database state needs a v2 vault and a known state")
    earlier = opened.doc["database"].get("migrated_from_plaintext_at")
    return _seal({**_body(opened.doc), "updated_at": now,
                  "database": _database(opened.dek, state, now, now if migrated else earlier)}, opened.dek)


def _new_passphrase_wrap(doc: dict, passphrase: str, dek: bytes) -> dict:
    wrap = {"kdf": "argon2id", "argon2_version": 19, **ARGON2, "salt": _b64(secrets.token_bytes(SALT_BYTES))}
    return {**wrap, **_wrap(doc, "passphrase", _passphrase_kek(passphrase, wrap), dek)}


def _new_recovery_wrap(doc: dict, recovery: bytes, dek: bytes) -> dict:
    wrap = {"kdf": "hkdf-sha256", "salt": _b64(secrets.token_bytes(SALT_BYTES))}
    return {**wrap, **_wrap(doc, "recovery", _recovery_kek(recovery, wrap), dek)}


def with_passphrase(opened: Opened, passphrase: str, now: str) -> dict:
    """The same DEK re-wrapped under a new passphrase (new salt and nonce). The recovery wrap is unchanged."""
    doc = {**_body(opened.doc), "updated_at": now}
    doc["wraps"] = {**doc["wraps"], "passphrase": _new_passphrase_wrap(doc, passphrase, opened.dek)}
    return _seal(doc, opened.dek)


def with_new_recovery_key(opened: Opened, now: str) -> tuple[dict, str]:
    recovery = secrets.token_bytes(KEY_BYTES)
    doc = {**_body(opened.doc), "updated_at": now, "recovery_confirmed": False}
    doc["wraps"] = {**doc["wraps"], "recovery": _new_recovery_wrap(doc, recovery, opened.dek)}
    return _seal(doc, opened.dek), format_recovery_key(recovery)


def with_recovery_confirmed(opened: Opened, now: str) -> dict:
    return _seal({**_body(opened.doc), "updated_at": now, "recovery_confirmed": True}, opened.dek)


def parse(raw: bytes) -> dict:
    """Structural checks only (no secrets needed). Raises VaultError for anything that is not a well-formed v1/v2 vault."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise VaultError("damaged", "The key vault is damaged or incomplete (not valid JSON)") from None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise VaultError("damaged", "The file is not an OncoScope key vault")
    version = doc.get("version")
    if isinstance(version, bool) or version not in SUPPORTED_VERSIONS:
        raise VaultError("unsupported", f"Key vault version {version!r} is not supported by this OncoScope "
                                        f"(supports {', '.join(map(str, SUPPORTED_VERSIONS))}); nothing was changed")
    if not isinstance(doc.get("checksum"), str) or hashlib.sha256(_canonical({k: v for k, v in doc.items() if k != "checksum"})).hexdigest() != doc["checksum"]:
        raise VaultError("damaged", "The key vault is damaged (checksum mismatch)")
    try:
        wraps = doc["wraps"]
        ok = (isinstance(doc["vault_id"], str) and isinstance(doc["owner_uid"], str) and isinstance(doc["mac"], str)
              and isinstance(doc["key_id"], str) and len(doc["key_id"]) == 32 and isinstance(doc["recovery_confirmed"], bool)
              and set(wraps) == {"passphrase", "recovery"} and wraps["passphrase"].get("kdf") == "argon2id"
              and wraps["passphrase"].get("argon2_version") == 19 and wraps["recovery"].get("kdf") == "hkdf-sha256")
        for name, (low, high) in ARGON2_LIMITS.items():
            value = wraps["passphrase"][name]
            ok = ok and isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
        if version >= 2:
            database = doc["database"]
            ok = (ok and isinstance(doc["owner_display_name"], str) and database["state"] in DATABASE_STATES
                  and database["cipher"] == DATABASE_CIPHER and isinstance(database["key_fingerprint"], str)
                  and all(type(w.get("aad_version")) is int and w["aad_version"] in (1, 2) for w in wraps.values()))
        else:
            ok = ok and "database" not in doc and not any("aad_version" in w for w in wraps.values())
    except (KeyError, TypeError, AttributeError):
        ok = False
    if not ok:
        raise VaultError("damaged", "The key vault is damaged (missing or invalid fields)")
    return doc


def open_with_passphrase(doc: dict, passphrase: str) -> Opened:
    dek = _unwrap(doc, "passphrase", _passphrase_kek(passphrase, doc["wraps"]["passphrase"]))
    _check_mac(doc, dek)
    return Opened(doc, dek)


def open_with_recovery_key(doc: dict, recovery_text: str) -> Opened:
    raw = parse_recovery_key(recovery_text)
    dek = _unwrap(doc, "recovery", _recovery_kek(raw, doc["wraps"]["recovery"]))
    _check_mac(doc, dek)
    return Opened(doc, dek)


def verify_against(doc: dict, dek: bytes) -> None:
    """Re-checks a vault document against the DEK we hold: integrity code and key id. Used after every write."""
    _check_mac(doc, dek)


# --- files ------------------------------------------------------------------------------------------

def read(path: Path) -> dict:
    if not path.exists():
        raise VaultError("missing", "The key vault file is missing")
    return parse(path.read_bytes())


def write(path: Path, doc: dict, dek: bytes, *, replace_existing: bool) -> None:
    """Atomic, verified write: temp file in the same folder → fsync → os.replace → read back and verify.
    A failure before the replace leaves the previous file untouched; a failed read-back restores the previous bytes."""
    if path.exists() and not replace_existing:
        raise VaultError("exists", "A key vault already exists here; OncoScope never replaces one automatically")
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_bytes() if path.exists() else None
    data = json.dumps(doc, indent=2, sort_keys=True).encode("ascii") + b"\n"
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    try:
        verify_against(parse(path.read_bytes()), dek)
    except VaultError:
        if previous is not None:
            write_raw(path, previous)
        else:
            path.unlink(missing_ok=True)
        raise VaultError("write_failed", "The key vault could not be written and verified; the previous vault was kept")


def write_raw(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def stale_temp_files(path: Path) -> list[Path]:
    """Leftovers of interrupted writes. They never hold a DEK in plaintext, but are removed when found."""
    return sorted(path.parent.glob(f".{path.name}.*.tmp")) if path.parent.exists() else []
