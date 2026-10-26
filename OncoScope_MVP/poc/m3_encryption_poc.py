"""M3 proof of concept — NOT used by the application. Fictional data only.

Questions it answers for docs/M3_SECURITY_ARCHITECTURE.md:
1. Can SQLCipher (via sqlcipher3) hold the existing OncoScope schema with a random 256-bit key on this platform?
2. Does a wrong key fail cleanly, and does the file contain no plaintext?
3. Does SQLite's backup API produce an encrypted copy (so backups stay encrypted)?
4. Can an existing plaintext v3 database be migrated with `sqlcipher_export` and verified row-for-row?
5. Can a passphrase-derived key (Argon2id) wrap the random data key, and does AES-256-GCM detect tampered documents?

Run in a throwaway virtualenv:  pip install sqlcipher3-wheels cryptography argon2-cffi
then: python poc/m3_encryption_poc.py <path to a plaintext OncoScope v3 database made from fictional fixtures>
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

import sqlcipher3
from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

results: dict[str, object] = {}
tmp = Path(tempfile.mkdtemp(prefix="oncoscope-poc-"))
data_key = secrets.token_bytes(32)                       # random DEK, never derived from the passphrase
hex_key = f"x'{data_key.hex()}'"


def open_encrypted(path: Path, key: str):
    # Finding (2026-10-26, sqlcipher3-wheels 0.5.7 / SQLCipher 4.12.0 community, Windows x64, Python 3.12):
    # with `PRAGMA cipher_memory_security = ON`, opening a database with a WRONG key crashes the whole process
    # ("Windows fatal exception: stack overflow", exit 127) instead of raising DatabaseError. With it OFF the wrong key
    # raises "file is not a database". Consequence for the design: never hand SQLCipher an unverified key — the key
    # vault's AES-GCM unwrap authenticates the passphrase first — and do not enable memory security with this build.
    conn = sqlcipher3.connect(str(path))
    conn.execute(f'PRAGMA key = "{key}"')
    return conn


# 1–2. Encrypted DB with the real schema; plaintext absent; wrong key refused.
plain_src = Path(sys.argv[1]) if len(sys.argv) > 1 else None
enc = tmp / "encrypted.sqlite3"
conn = open_encrypted(enc, hex_key)
conn.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY, text TEXT)")
conn.execute("INSERT INTO notes(text) VALUES ('Fictional SENTINEL-POC CA-125 310 U/mL')")
conn.commit()
conn.close()
results["plaintext_absent_from_file"] = b"SENTINEL-POC" not in enc.read_bytes()
bad = open_encrypted(enc, "x'" + "00" * 32 + "'")
try:
    bad.execute("SELECT count(*) FROM notes").fetchone()
    results["wrong_key_refused"] = False
except sqlcipher3.DatabaseError as exc:
    results["wrong_key_refused"] = f"yes ({exc})"
bad.close()

# 3. Backup API keeps encryption.
src = open_encrypted(enc, hex_key)
dst_path = tmp / "backup.sqlite3"
dst = open_encrypted(dst_path, hex_key)
src.backup(dst)
dst.close()
src.close()
results["backup_encrypted"] = b"SENTINEL-POC" not in dst_path.read_bytes()
check = open_encrypted(dst_path, hex_key)
results["backup_readable_with_key"] = check.execute("SELECT text FROM notes").fetchone()[0].startswith("Fictional")
check.close()

# 4. Migration of a plaintext OncoScope database with sqlcipher_export, verified per table.
if plain_src:
    migrated = tmp / "migrated.sqlite3"
    p = sqlcipher3.connect(str(plain_src))
    p.execute(f"ATTACH DATABASE '{migrated.as_posix()}' AS enc KEY \"{hex_key}\"")
    p.execute("SELECT sqlcipher_export('enc')")
    version = p.execute("PRAGMA user_version").fetchone()[0]
    p.execute(f"PRAGMA enc.user_version = {version}")
    p.execute("DETACH DATABASE enc")
    tables = [r[0] for r in p.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    digest = lambda c, t: hashlib.sha256(json.dumps(c.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall(), default=str).encode()).hexdigest()
    before = {t: digest(p, t) for t in tables}
    p.close()
    m = open_encrypted(migrated, hex_key)
    after = {t: digest(m, t) for t in tables}
    results["migration_tables"] = len(tables)
    results["migration_identical"] = before == after
    results["migration_user_version"] = m.execute("PRAGMA user_version").fetchone()[0] == version
    results["migration_triggers_kept"] = m.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]
    labels = [r[0] for r in m.execute("SELECT label FROM patients LIMIT 1")]
    m.close()
    results["migrated_file_has_no_case_label"] = all(l.encode() not in migrated.read_bytes() for l in labels)

# 5. Passphrase-derived KEK wraps the DEK; AES-GCM document envelope detects tampering.
salt = secrets.token_bytes(16)
kek = hash_secret_raw(b"fictional passphrase for the poc", salt, time_cost=3, memory_cost=64 * 1024, parallelism=2, hash_len=32, type=Type.ID)
nonce = secrets.token_bytes(12)
wrapped = AESGCM(kek).encrypt(nonce, data_key, b"oncoscope-dek-v1")
results["dek_unwraps"] = AESGCM(kek).decrypt(nonce, wrapped, b"oncoscope-dek-v1") == data_key
doc = b"FICTIONAL DOCUMENT CA-125 310 U/mL"
dn = secrets.token_bytes(12)
sealed = dn + AESGCM(data_key).encrypt(dn, doc, b"source:1")
try:
    tampered = bytearray(sealed)
    tampered[-1] ^= 1
    AESGCM(data_key).decrypt(bytes(tampered[:12]), bytes(tampered[12:]), b"source:1")
    results["document_tamper_detected"] = False
except InvalidTag:
    results["document_tamper_detected"] = True
results["sqlcipher_version"] = sqlcipher3.connect(":memory:").execute("PRAGMA cipher_version").fetchone()[0]
print(json.dumps(results, indent=2, default=str))
