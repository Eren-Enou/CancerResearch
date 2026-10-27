# M3.1-B — Key vault and recovery (synthetic data only)

**What this milestone does:** a protected installation now has a local key vault. The vault holds one random 256-bit
data-encryption key (DEK), which the owner can open with the passphrase or, separately, with a recovery key.

**What it does not do:** **nothing is encrypted with the DEK yet.** The database, documents, backups and exports are
still plaintext files. Anyone who can read `backend/data/` can read every case without the passphrase. Database
encryption is M3.1-C, document and backup encryption M3.1-D, and a keyed audit chain M3.1-E. OncoScope is not approved
for clinical use, and no HIPAA or other compliance is claimed.

## Implemented

### Key hierarchy and libraries

No cryptographic algorithm is implemented in OncoScope.

| Element | How | Library |
|---|---|---|
| DEK | 32 random bytes | `secrets.token_bytes` |
| Passphrase → key-encryption key | Argon2id, t=3, 64 MiB, p=4, 16-byte random salt per wrap (RFC 9106 low-memory profile); parameters are stored per vault so they can be raised later | `argon2-cffi` 25.1 (`hash_secret_raw`, `Type.ID`) |
| Recovery key | 32 random bytes, shown as 52 base32 characters plus a 4-character checksum, in 14 groups of 4 | `secrets`, `base64`, `hashlib` |
| Recovery key → key-encryption key | HKDF-SHA256, 16-byte random salt, label `oncoscope/vault/v1/recovery-kek` | `cryptography` 50.0.2 |
| Wrapping the DEK | AES-256-GCM, a random 96-bit nonce per wrap; associated data = format, version, vault id, key id and wrap kind | `cryptography` (`AESGCM`) |
| Key id | HKDF-SHA256(DEK, `oncoscope/vault/v1/key-id`), 16 bytes; a public identifier, not the key | `cryptography` |
| Vault integrity | HMAC-SHA256 under HKDF(DEK, `oncoscope/vault/v1/vault-mac`) over the whole file, plus a plain SHA-256 checksum that tells accidental damage apart from a wrong credential | `cryptography`, `hmac`, `hashlib` |
| Purpose keys for later increments | HKDF-SHA256(DEK, fixed label) for `database` (M3.1-C), `documents` and `document-names` (M3.1-D), `backup-manifest` (M3.1-D) and `audit` (M3.1-E). An unknown purpose is an error, never a new key | `cryptography` |

### Vault file

- **Location:** `oncoscope-vault.json` beside the database. `ONCOSCOPE_VAULT_PATH` overrides it.
- **Contents:** format and version, vault id, owner uid, key id, timestamps, the recovery-confirmed flag, two wraps (each
  with its KDF parameters, salt, nonce and ciphertext), the MAC and the checksum.
- **Never stored:** the DEK, the passphrase, the recovery key and the derived keys are never written to the vault or
  anywhere else (tested on every file, backup, export and server log).
- **Binding:** migration 5 records `vault_id` and `vault_key_id` in `local_owner`, binding the installation to its vault.

### Flows

All flows keep the same DEK.

| Flow | What happens |
|---|---|
| **Setup** (`POST /api/auth/setup`) | Creates the owner, creates the vault (refusing if a vault file already exists) and binds it. Returns the session and the **recovery key, once**. |
| **Unlock** (`POST /api/auth/unlock`) | The vault's passphrase wrap is authoritative. The M3.1-A verifier is kept in step and re-synchronised if an interrupted change left it behind (audited as `verifier_resynced`). An M3.1-A installation without a vault gets one at its next successful unlock. |
| **Recovery key not confirmed** | While the owner hasn't confirmed saving it, every unlock issues a **fresh** recovery key, and the earlier, unseen one stops working. `POST /api/vault/recovery-key/confirm` ends this. |
| **Recover** (`POST /api/auth/recover`, public) | Recovery key + new passphrase → same DEK, re-wrapped under the new passphrase, plus a new session. The recovery key stays valid. Wrong keys share the unlock rate limit (5 failures, then 30 s doubling to 15 min). A mistyped key is caught by its checksum (422) and not counted as a guess. |
| **Change passphrase** (`POST /api/vault/passphrase`) | Needs a session and the current passphrase. A wrong current passphrase gets 403, rate limited; it is not 401, because 401 means "session ended". The vault is written first, then the verifier. |
| **Lock, expiry, a new unlock, server restart** | The session's DEK `bytearray` is overwritten with zeros and dropped. |
| **Status** (`GET /api/vault/status`) | Identifiers, KDF settings, the confirmation flag and **fingerprints** of the derived keys (HMAC of each key, not the key). Lets you see that the same keys come back after a passphrase change, a recovery or a restart. |

### Failure handling

| Case | Result | Stored data |
|---|---|---|
| Wrong passphrase / recovery key | 401 "Incorrect …", counted | unchanged |
| Mistyped recovery key (checksum, length, alphabet) | 422 "check for a typing mistake", not counted | unchanged |
| Truncated, empty, non-JSON, bad base64, missing or out-of-range fields, checksum mismatch | 409 "The key vault is damaged …" | unchanged (tested byte for byte) |
| Unsupported version | 409 "Key vault version … is not supported" | unchanged |
| Edited header with a recomputed checksum | caught by the MAC (409 "altered") or by the wrap's associated data (401) | unchanged |
| Vault missing after binding | 409; **a new DEK is never created**; restore the file from a backup | unchanged |
| Vault from another installation (different id or key) | 409 "does not belong to this installation" | unchanged |
| Vault file present at setup | 409; never replaced | unchanged |
| Crash before the atomic rename | previous vault intact; temp file removed | previous vault |
| Write that doesn't verify on read-back | previous bytes restored; 503 | previous vault |
| Crash after the vault is written, before the verifier update | new passphrase works, verifier re-synchronised | consistent at next unlock |
| Crash during first-vault creation, before the database records it | the file left behind is adopted at the next unlock, never overwritten | adopted |

Writes go to a temp file in the same folder, then `fsync`, `os.replace`, then a read-back check (parse + MAC + key id).

### Backups

- **Backup:** `python -m app.backup` copies the vault file. The manifest records its SHA-256, vault id and key id, and
  verification fails if the vault is missing or different.
- **Restore:** copies the vault only into a folder that has none, and refuses if the target already has a different
  vault. A restore onto a clean folder can be opened with the recovery key alone (tested).

### Frontend

- **Recovery-key screen:** after setup, or an unlock while the key is unconfirmed, it comes before the workspace.
  - Shows the key in groups and offers a copy button.
  - Warns that losing both credentials makes future encrypted data unrecoverable.
  - Confirmation requires a checkbox and typing the key's last group.
  - The key lives only in component state; it is never put in storage, URLs or files.
- **Lock screen:** "Forgot the passphrase? Use your recovery key."
- **Security view:** vault identifiers, KDF settings, derived-key fingerprints, "Not encrypted yet", and passphrase change.

## Verification

Run on Windows 11 / Edge 138, Python 3.12.6, argon2-cffi 25.1.0, cryptography 50.0.2.

- **`backend/tests/test_vault.py` — 39 tests.** Each of the 12 required properties maps to at least one test:

  | # | Property | Test(s) |
  |---|---|---|
  | 1 | Correct passphrase unlocks the DEK | `test_passphrase_and_recovery_key_open_the_same_dek` |
  | 2 | Wrong passphrase fails safely | `test_wrong_credentials_fail_cleanly` |
  | 3 | Recovery key unlocks the same DEK | `test_recovery_key_restores_the_same_dek…` |
  | 4 | Wrong recovery key fails safely | `test_wrong_and_mistyped_recovery_keys` |
  | 5 | Passphrase change keeps the DEK | `test_passphrase_change_keeps…`, `test_changing_the_passphrase` |
  | 6 | Corruption is refused, nothing modified | `test_damaged_vaults…` (10 cases), `test_unsupported_versions…`, `test_deliberate_edits…`, `test_a_damaged_vault_is_refused_without_changing_anything` |
  | 7 | No plaintext secrets on disk | `test_no_plaintext_secrets_are_written_anywhere` |
  | 8 | Restarts keep the vault working | `test_restart_preserves_the_vault_and_both_ways_in` |
  | 9 | Interrupted writes keep the previous vault | `test_interrupted_write…`, `test_a_write_that_does_not_verify…`, both `test_interrupted_passphrase_change…`, `test_interrupted_initialisation…` |
  | 10 | M3.1-A auth and audit still work | `test_protected.py`, `test_vault_routes_need_a_session`, `test_an_m31a_installation_gets_its_vault…` |
  | 11 | Demo mode stays isolated | `test_demonstration_mode_has_no_vault` |
  | 12 | Windows and CI | all of the above, locally on Windows and in GitHub Actions on Ubuntu |

  The file also covers backups and restores (`test_backups_carry_the_vault…`) and a real server's log
  (`test_server_logs_contain_no_recovery_keys_or_passphrases`).
- **`frontend/src/vault.test.tsx` — 5 tests:** the recovery-key screen, setup → key → workspace, the recovery form, a
  wrong recovery key, and the Security view.
- **`frontend/e2e/vault.spec.ts` (Playwright project `m3.1b`):**
  1. Setup, then copying and confirming the recovery key.
  2. Lock and unlock (no key shown again).
  3. A wrong current passphrase, then a passphrase change.
  4. The old passphrase is refused and the new one works.
  5. "Forgotten" passphrase: a mistyped recovery key, then the correct one typed loosely, setting a third passphrase.
  6. The key ID and derived-key fingerprints are unchanged throughout, and only the newest passphrase works.
  7. No secret appears in any request URL, in browser storage or in the vault file.

## Integration requirements for the next increments

### M3.1-C — SQLCipher database

**Implemented** (`M3_1C_DATABASE_ENCRYPTION.md`): items 1–5 below, with the owner metadata in vault format v2 and the
verifier retired by migration 6.

1. **Key:** `vault.derive(dek, "database")` as a raw key (`PRAGMA key = "x'…'"`), set only **after** the vault has
   opened. AES-GCM authenticates the DEK, so SQLCipher never sees an unverified key. Keep
   `cipher_memory_security` **off**: the M3.0 proof of concept showed a wrong key crashing the process with it on.
2. **Owner metadata outside the database:** an encrypted database cannot be read before unlock. The owner's uid,
   display name and the vault binding must move out of `local_owner` into the vault header (the MAC already covers it),
   or into a small plaintext sidecar that the MAC protects. Health, auth status, setup and unlock must work without
   opening the database. Once the vault is authoritative, the M3.1-A verifier is redundant; retire it with a migration.
3. **Recording the encryption state:** add a field to the vault saying the database is encrypted, with the database
   key's fingerprint. This needs vault format version 2. v1 vaults must still parse and be upgraded atomically.
4. **Migration:** `sqlcipher_export` from plaintext into a new file, then verify table hashes, the audit chain and head,
   `user_version`, triggers and `PRAGMA integrity_check`. Keep a verified plaintext backup until the encrypted copy
   verifies, then swap it in atomically. Use explicit state markers (`plaintext` → `migrating` → `encrypted`) so that an
   interrupted migration resumes or rolls back and never leaves two half-valid databases.
5. **Backups:** use SQLite's backup API on a keyed connection (the copy stays encrypted). Restore needs the vault (it
   is already in backups) and the passphrase or recovery key. Test restoring onto a clean folder with each credential.

### M3.1-D — Encrypted documents and backups

1. **Documents:** AES-256-GCM with `derive(dek, "documents")`, a random 96-bit nonce per file, and associated data =
   format version + plaintext SHA-256 (+ source id), stored as `nonce ‖ ciphertext ‖ tag`. After decryption, the
   plaintext SHA-256 is still checked against the record, so the M2 integrity model is unchanged.
2. **File names:** `HMAC-SHA256(derive(dek, "document-names"), sha256)` instead of the bare hash, so a copy of the
   store doesn't confirm guessed documents.
3. **Backup manifests:** HMAC under `derive(dek, "backup-manifest")`, so an attacker who can recompute plain hashes is
   still detected.
4. **Store migration:** re-encrypt into a new store, verify every document, then swap it in. The same interrupted-run
   rules as M3.1-C apply.

### M3.1-E — Keyed audit integrity

1. **New entries:** `HMAC-SHA256(derive(dek, "audit"), prev ‖ entry)`.
2. **Existing entries:** the existing SHA-256 entries stay as they are. A transition entry switches the chain to HMAC,
   so earlier hashes remain verifiable.
3. **Head receipts:** recorded in backup manifests and shown to the owner, to close tail truncation (L2).
4. **Entries without a key:** entries written while locked, such as failed unlocks, cannot be MACed (no key). Either
   keep them in a separate unkeyed log or MAC them at the next unlock; decide in the M3.1-E design.

## Deviations from the plan and the architecture document

- The architecture document suggested benchmarking Argon2id at setup (≥ 0.5 s). This milestone uses the fixed RFC 9106
  low-memory profile (about 0.05 s on the development machine). The parameters are stored per vault, so they can be
  raised without a format change.
- The vault file is `oncoscope-vault.json` rather than `keys.json`.
- There is no general recovery-key rotation yet. A new key is issued automatically only while the current one is
  unconfirmed.

## Remaining risks

1. ~~**Plaintext at rest.** The DEK protects nothing yet.~~ Since M3.1-C the database is encrypted with a key derived
   from it; documents remain plaintext until M3.1-D.
2. **Offline guessing.** Anyone with the vault file can guess passphrases offline at Argon2id cost (64 MiB, 3 passes),
   and the database's verifier allows the same. The 12-character minimum is a policy, not a strength check. The
   recovery key (256 bits) is not practically guessable.
3. **Keys in memory.** The DEK lives in server memory while unlocked. The `bytearray` is zeroed on lock, but Python and
   the libraries make transient `bytes` copies that cannot be wiped reliably. Same-user malware can read it.
4. **Recovery-key exposure.** The key is visible on screen while shown and may stay in the clipboard after copying (the
   UI says to clear it). It does not rotate after use. Anyone holding it can set a new passphrase.
5. **Losing the vault file loses the DEK.** This has no effect today, but will after M3.1-C. Backups now include the
   vault; keep at least one backup and the recovery key apart from the computer.
6. **Setup crash window.** A crash between writing the vault and committing the owner leaves a vault with no owner.
   Setup then refuses until that file is moved away by hand. This is deliberate: a vault is never replaced
   automatically.
7. **Atomicity on Windows.** `os.replace` is atomic on NTFS. Directory `fsync` is not available on Windows, so the
   rename's own durability after power loss depends on the file system.
8. **A damaged wrap looks like a wrong credential.** AES-GCM cannot tell a wrong passphrase from altered ciphertext:
   both get 401. The other wrap still works in that case.
9. **In-memory rate limit.** It resets when the server restarts (as in M3.1-A).
10. **No independent cryptographic or security review** has been done.

## Next increment: M3.1-C

Migrate the existing SQLite database to SQLCipher using this vault: lossless, verified migration with backups and
recovery from interrupted operations. The requirements are listed above.
