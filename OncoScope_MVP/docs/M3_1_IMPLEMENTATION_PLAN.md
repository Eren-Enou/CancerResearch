# M3.1 implementation plan — first security controls (synthetic data only)

Goal: make the three strict-xfail gap tests in `backend/tests/test_security.py` pass by design, without weakening any
audit, provenance or validation rule, and keep every existing M2–M2.6 test green. Real data stays out of scope until the
M3 review passes. Design reference: `M3_SECURITY_ARCHITECTURE.md` §1–5, 8.

## Increments (each a separate PR, in order)

### M3.1-A Protected mode, local identity and session tokens
**Status: implemented** (`M3_1A_PROTECTED_ACCESS.md`). Criteria 1–6 are covered by automated tests. Criterion 7 is met
in effect only: non-loopback peers get 403, but a launcher that refuses to bind remains for M3.1-F.
Scope: first-run setup (display name + passphrase), unlock/lock endpoints, in-memory session token, auth middleware, actor
id on every audit entry, UI lock screen. Synthetic "demo mode" without a passphrase remains available **only** for cases
flagged synthetic, so existing fixtures and tests keep working.

Acceptance criteria / failure scenarios (all automated):
1. Any `/api/*` call except health/unlock without a token → 401; with a wrong or expired token → 401; with the token → 200.
2. A request carrying the token from a foreign `Origin` cannot be made without a CORS preflight, which is refused.
3. Wrong passphrase → 401 after Argon2id work, back-off grows; the process never crashes (see PoC finding).
4. Lock → subsequent calls 401; the UI clears its in-memory state.
5. Every audit entry written while unlocked has a non-null `actor_id`; the chain still verifies.
6. `test_unauthenticated_requests_are_rejected` passes (xfail marker removed).
7. API refuses to bind a non-loopback host without an explicit override flag (test with `0.0.0.0`).

### M3.1-B Key vault and recovery key
Scope: `keys.json` (Argon2id params + salt, DEK wrapped by passphrase and by recovery key), HKDF subkeys, passphrase change,
recovery-key display once.
1. Vault contains no plaintext key material (scan file for the DEK bytes → absent).
2. Wrong passphrase / wrong recovery key → clean error, no SQLCipher call made.
3. Passphrase change re-wraps; old passphrase no longer unlocks; data still readable.
4. Corrupted vault (flipped byte) → clean, specific error.

### M3.1-C Encrypted database with migration
Scope: replace `sqlite3` with `sqlcipher3` behind `db.connection()`; migration tool `python -m app.encrypt_migrate`.
Migration strategy (synthetic databases only):
1. Verify the plaintext DB (integrity check, audit chain, manifest-style table hashes) and make a verified backup.
2. `sqlcipher_export` into a new encrypted file (PoC-proven), copy `user_version`, verify per-table hashes, triggers and
   audit head are identical.
3. Swap files atomically; keep the plaintext backup until the user confirms, then delete it securely and record a
   tombstone audit entry. Interrupted migration → the plaintext original is untouched and the new file is discarded.
Acceptance: `test_database_file_does_not_contain_plaintext_records` passes; all existing tests pass against the encrypted DB;
migration of the committed fictional fixtures DB is lossless (table hashes equal); interrupted-migration test.

### M3.1-D Encrypted document store and encrypted backups
Scope: AES-256-GCM document files with HMAC-derived names; decrypt-and-verify on read; backups and restores of the
encrypted store; HMAC-authenticated manifest; restore with passphrase or recovery key.
1. No document plaintext or plain SHA-256 file name in the store or backups (scan).
2. Flipped ciphertext byte → 409 on read and refused restore; missing file → refused restore.
3. Independent restore test: back up, move data dir away, restore into an empty dir with the passphrase, then with only the
   recovery key; per-table hashes, audit head and document plaintext hashes identical.
4. Restoring with the wrong key fails before any file is written.

### M3.1-E Keyed audit chain and head receipts
Scope: HMAC chain under the audit subkey (migration recomputes the chain for existing entries and records the plain-SHA
head it replaced), head in manifests and as a downloadable receipt, comparison on demand and on restore.
1. Recomputing entries without the key → verification fails.
2. Removing the newest entries is detected against the latest stored head; `test_tail_truncation_is_detected_without_external_head`
   is rewritten to use the stored head and passes.

### M3.1-F Start-up hardening
Data directory created owner-only (POSIX 0700; Windows ACL restricted to the current user) and verified at start-up; stale
`.partial`/`.restoring` files cleaned; refuse to start if the data directory is world-readable.

## Explicitly not in M3.1 (planned M3.2)
Per-case keys and case deletion/crypto-shredding (design §7), parsing sandbox (§6), consent records (§9), encrypted
exports (§10), DEK rotation, OS keystore integration, Python dependency lock file with hashes.

## Evidence required to call M3.1 done
All suites green locally and in CI (backend incl. security suite with the three xfails converted, frontend, three
Playwright demonstrations plus a new lock/unlock demonstration), an updated threat model with residual risks, and a
reviewer's sign-off on the vault format and migration procedure. The independent security review and the human
usability walkthrough remain separate, outstanding requirements.
