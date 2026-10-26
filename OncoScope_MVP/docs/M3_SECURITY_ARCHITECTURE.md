# Proposed M3 security architecture

Status: **design for review**, not implemented. It addresses the risks in `M3_THREAT_MODEL.md` with established libraries
and operating-system protections; no custom cryptography. Nothing here makes OncoScope compliant with HIPAA, GDPR or any
other regime — that needs an independent legal and security assessment for a specific deployment.

## Scope and assumptions

- One person uses one OncoScope data directory on one computer. Multi-user servers, sync and remote access are out of scope.
- The OS account and the device are the outer boundary: OncoScope cannot protect an unlocked session against malware
  running as the same user (it can limit what that malware gets from files at rest and from a locked app).
- Full-disk encryption (BitLocker/FileVault/LUKS) will be a **stated prerequisite** for real data; app-level encryption
  is defence in depth and protects backups and copies that leave the disk.

## Library and mechanism choices

| Need | Choice | Why | Evidence so far |
|---|---|---|---|
| Database encryption | **SQLCipher 4** via `sqlcipher3` (raw 256-bit key, default SQLCipher 4 settings: AES-256-CBC + HMAC-SHA512 per page) | Encrypts the whole file: tables, `audit_log`, `document_text`, indexes, journals, free pages. Drop-in for `sqlite3`; migration via `sqlcipher_export` | PoC (`poc/m3_encryption_poc.py`): Windows wheel `sqlcipher3-wheels` 0.5.7 = SQLCipher 4.12.0; no plaintext in file; backup API output stays encrypted; existing v3 DB migrated with identical table hashes, `user_version` and triggers |
| Document encryption | **AES-256-GCM** from `cryptography` (pyca), one random 96-bit nonce per file, associated data = source id + format version | Authenticated; tampering detected; standard library | PoC: tamper detected (`InvalidTag`) |
| Passphrase → key | **Argon2id** (`argon2-cffi`), salt 16 B, parameters stored with the vault and benchmarked at setup (target ≥ 0.5 s, ≥ 64 MiB) | Memory-hard KDF recommended by OWASP | PoC: wraps/unwraps the data key |
| Key storage convenience (optional) | OS keystore via `keyring` (Windows Credential Manager/DPAPI, macOS Keychain, Secret Service) | Lets an unlocked session survive restarts if the user opts in | Not tested |
| Tokens | `secrets.token_urlsafe(32)`, compared with `hmac.compare_digest` | Standard library | — |

**PoC finding that constrains the design:** with `PRAGMA cipher_memory_security = ON`, opening a database with a *wrong*
key **crashed the Python process** (native stack overflow) instead of raising an error. Therefore SQLCipher must only ever
receive a key that the vault has already authenticated (AES-GCM unwrap fails cleanly on a wrong passphrase), memory
security stays off with this build, and the crash is to be reported upstream before adoption. SQLCipher also prints its
own error lines (`hmac check failed for pgno=1`) to stderr on a wrong key — harmless content-wise, but log handling must
expect it.

## 1. Local authentication and identity
- First run in "protected" mode: the user chooses a display name and a passphrase (≥ 12 characters; strength meter, no
  composition rules). The display name becomes the **actor** on every audit entry.
- Unlocking = Argon2id(passphrase) → KEK → AES-GCM-unwrap the data keys. Wrong passphrase → clean refusal, attempt
  counter with exponential back-off (local brute force is still possible offline against the vault file — the KDF cost is
  the defence).
- **Is authentication needed on loopback? Yes.** Loopback stops other computers, but not (a) other processes of the same
  user, (b) other OS accounts that can reach `127.0.0.1`, or (c) web pages in the user's browser making requests to
  `127.0.0.1`. Authentication + a non-simple header closes (b) and (c) completely and narrows (a) to processes able to
  read the running app's memory.

## 2. Session authorization and API access
- On unlock the API creates a random session token held **only in memory** (server) and returned once to the UI, which
  keeps it in memory (not `localStorage`, not cookies → no CSRF surface) and sends `Authorization: Bearer …` on every call.
  A custom header forces a CORS preflight the API never approves, so foreign web origins cannot use a token they don't have.
- Middleware rejects every `/api/*` request except `/api/health` and `/api/session/unlock` without a valid token (401).
  Idle lock after N minutes (default 15) and explicit **Lock**: the token and all keys are dropped from memory.
- One role ("owner") in M3.1; the session's actor id is attached to each write and audit entry.
- The API refuses to start if asked to bind a non-loopback address unless an explicit, documented override is given.

## 3. Encryption of stored records and documents
- Key hierarchy: **KEK** (from passphrase) wraps one random **data key (DEK)**; from the DEK, HKDF-SHA256 derives
  separate subkeys for the database (`db`), documents (`doc`), filename hashing (`name`) and audit MAC (`audit`).
- Database: SQLCipher keyed with the `db` subkey; `PRAGMA secure_delete = ON`.
- Documents: stored as `nonce ‖ ciphertext ‖ tag`. File names become `HMAC-SHA256(name-key, plaintext)` instead of the plain
  SHA-256 (a plain hash lets anyone with a copy of the store confirm a guessed document). The plaintext SHA-256 stays
  inside the encrypted DB and is still verified after decryption, so the existing integrity model is unchanged.
- Decryption only in API memory; documents are streamed to the browser over loopback with `no-store`.

## 4. Backups and recovery
- Backups contain the SQLCipher file (already encrypted, copied with the backup API) and the encrypted document files.
  The manifest is authenticated with an HMAC under a backup subkey, so tampering is detected even by an attacker who can
  recompute plain hashes.
- **Recovery key**: at setup a random 256-bit key is shown once (grouped base32, printable) and also wraps the DEK. Losing
  both passphrase and recovery key = data is unrecoverable (stated plainly in the UI).
- Independent restore test (also in CI with fictional data): back up → move the data dir away → restore on a clean
  directory with the passphrase, then again with only the recovery key → compare per-table hashes, audit head and
  document hashes; wrong key, truncated file, flipped ciphertext byte and missing document each fail cleanly.

## 5. Key management
- Vault file `keys.json` in the data dir: format version, Argon2id parameters + salt, `wrapped_dek_by_passphrase`,
  `wrapped_dek_by_recovery`, key id, creation date. No plaintext key material ever written.
- Passphrase change = re-wrap the DEK (instant). Recovery-key rotation = new recovery key, re-wrap. **DEK rotation**
  (suspected compromise) = SQLCipher `PRAGMA rekey` + re-encrypt documents to a new store + new backup (offline job, M3.2).
- Keys live in process memory only while unlocked; locked/stopped → wiped references (Python cannot guarantee zeroing;
  documented limitation).

## 6. Document parsing isolation
- pypdf (and any future OCR) runs in a **separate subprocess** started per job with: no network (the app has no network
  code; the child inherits nothing else), a wall-clock timeout, a memory cap (Windows Job Object / POSIX `RLIMIT_AS`),
  and a pipe that returns text only. A crash or limit hit = "could not be read", never a partial result.
- Decrypted bytes go to the child over the pipe; no plaintext temp files.
- Browser: pdf.js legacy build kept current; XFA off; the page re-hashes before rendering (already done).

## 7. Retention, deletion and cache purging (coexisting with an append-only audit)
- **Per-case keys**: each case gets its own key (wrapped by the DEK) used for its documents and for the *content* fields
  of its audit details (`detail_json` stored encrypted). The audit chain hashes the stored (encrypted) bytes, so it still
  verifies after deletion.
- **Delete case** = remove its rows, its `document_text` rows, its document files (unless shared by another case — reference
  counted), and **destroy the case key** (crypto-shredding: any copies of its encrypted audit details and documents in old
  backups become unreadable once those backups are re-keyed or expire). A content-free tombstone audit entry records who
  deleted which case id and when. `PRAGMA secure_delete` + `VACUUM` after deletion.
- Retention: configurable age limit for backups and pre-restore backups; exports are listed in the audit log so a user can
  find and remove them; the app has no other caches (browser responses are `no-store`).
- Honest limit: copies the user made outside the app (downloads, printed reports, unmanaged backups) cannot be deleted by
  the app.

## 8. Audit integrity and operator accountability
- Chain entries become `HMAC-SHA256(audit-key, previous ‖ entry)`: an attacker without the key cannot rewrite history
  consistently (today anyone can recompute plain SHA-256).
- Every audit entry carries the actor id; operator events are audited too: unlock/lock, failed unlocks, export, backup,
  restore, deletion, key changes.
- **Head anchoring**: the head (entry count + last MAC) is written into every backup manifest and shown in the UI as an
  "audit receipt" the user can save elsewhere; restore and an on-demand check compare against the latest known head, which
  closes the tail-truncation gap (L2).

## 9. Consent and permission records
- New `consents` table (encrypted with the rest): case id, description of the person (no identifiers needed for synthetic
  use), scope (`store`, `export`, `share`), who recorded it, date, expiry, revocation date, link to a source document.
- Creating a non-synthetic case requires a consent record; export of a non-synthetic case requires an `export` scope.
- The *content* of a valid consent (wording, age, capacity, jurisdiction) is a **legal requirement outside this design**.

## 10. Import and export boundaries
- Import: existing allow-list (PDF/TXT/PNG/JPEG), magic-byte checks, 10 MB cap, hash, duplicate refusal; plus sandboxed
  parsing (§6) and, later, metadata review (PDF author/EXIF) before storing.
- Export: synthetic-only today; with real data, exports are encrypted by default (AES-GCM container with a key derived
  from an export passphrase, or the `age` format), require an explicit confirmation naming what leaves the app, and create
  an audit entry. Markdown/CSV plaintext export only after a warning.

## What can be verified where

| Can be implemented and verified locally (tests with fictional data) | Needs independent assessment | Depends on legal requirements |
|---|---|---|
| Token auth and 401s, lock/unlock, actor on audit entries; encrypted DB/doc store (no plaintext on disk); wrong-key handling; encrypted backup + restore with passphrase and recovery key; HMAC audit chain and head comparison; deletion and crypto-shredding effects; parser sandbox limits; non-loopback refusal | Cryptographic configuration and key handling review; penetration test of the local API and browser surface; parser-sandbox escape review; usability of passphrase/recovery flows with real people | Whether local storage of health records is permitted for the intended use; consent wording and records; retention periods; breach notification; jurisdiction-specific rights (access, erasure) |
