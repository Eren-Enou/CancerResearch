# M3.1-C — Encrypted database and lossless migration

Status: implemented on fictional data only. **This does not make OncoScope ready for real patient records**, and
nothing here claims compliance with any healthcare regulation. Original documents are still plaintext until M3.1-D.

## What changed

In protected mode the database is now a **SQLCipher 4** file. The whole file is encrypted, including tables, indexes,
the audit log, the cached document text, rollback journals and free pages. It is keyed with a raw 256-bit key derived
from the M3.1-B vault's data key (DEK): `vault.derive(dek, "database")`. Without that key the file is opaque:
ordinary SQLite reports "file is not a database", and none of the record text is in its bytes. Demonstration mode keeps
its own separate, unencrypted synthetic database, as before.

| Component | Choice | Notes |
|---|---|---|
| Library | `sqlcipher3` **0.6.4** (upstream pysqlite fork, wheels for Windows, Linux and macOS) bundling **SQLCipher 4.19.0 community** on SQLite 3.53.4 | A maintained library; no custom cryptography. Validated locally on Windows 11 x64 and in CI on Ubuntu. If the module lacks SQLCipher, `app.db` fails at import ("no SQLCipher support") rather than storing anything unencrypted. |
| Cipher settings | SQLCipher 4 defaults: AES-256-CBC per 4096-byte page, HMAC-SHA512 per page, random per-database salt | The key is passed raw (`PRAGMA key = "x'…'"`), so SQLCipher's own PBKDF2 is skipped. The key is already a 256-bit HKDF output of a random DEK. |
| `cipher_memory_security` | **OFF** on every connection | The M3.0 proof of concept (sqlcipher3-wheels 0.5.7 / SQLCipher 4.12.0) crashed the process on a wrong key with it on. The crash did not reproduce with 4.19.0 (probe on 2026-10-26), but it stays off as required. In any case the vault authenticates the key before SQLCipher sees it. |
| Other pragmas | `temp_store = MEMORY`, `secure_delete = ON`, `foreign_keys = ON` | Sorts and temporary tables never go to disk. Freed pages are zeroed before they are encrypted. |

## Startup and authentication

The old server opened SQLite at startup and read the owner from the database. An encrypted database cannot support
that, so protected mode now **starts locked** and reads nothing protected before unlock.

- **Before unlock:** the lock screen's state comes from the vault header and the database file's *kind* (missing,
  empty, plaintext SQLite header, or opaque/encrypted). It never comes from the database itself (`auth.storage()`).
  The states are:
  - `setup`: no vault and no database.
  - `locked`: a vault v2 matching an encrypted database.
  - `migration_required`: a plaintext database with no vault, or with a v1 vault, or with a vault that says
    "plaintext".
  - `migration_interrupted`: `encrypt-migration.json` exists.
  - `refused`: anything inconsistent. That covers a missing or damaged vault; a missing or empty database the vault
    says is encrypted; a plaintext file where an encrypted one is expected; or an encrypted file with no vault.

  `/api/auth/status` reports this state and the UI shows an explanation instead of an unlock form. Nothing in the
  explanation offers a way around the refusal.
- **Vault format v2** holds the metadata needed before unlock, outside the database:
  - owner uid and display name;
  - vault id and key id;
  - `database.state` (`plaintext` | `pending` | `encrypted`), the cipher and the database key's fingerprint.

  The vault MAC covers all of it. It is therefore authenticated the moment the vault opens with the passphrase or
  recovery key, before any SQLCipher call. Before unlock it is *displayed*, never trusted to open anything. v1 vaults
  (M3.1-B) still parse and open, and the migration tool upgrades them. Their wraps keep their original v1 binding
  (`aad_version: 1`), so the M3.1-B recovery key keeps working: re-wrapping it would need the recovery key itself.
- **Unlock order:**
  1. The vault is opened. A wrong passphrase or recovery key fails here (AES-GCM) and never reaches SQLCipher.
  2. The MAC is checked.
  3. The database key is derived, and its fingerprint is checked against the vault.
  4. The encrypted database is opened. A wrong key, a replaced file or damaged pages give 409 and change nothing.
  5. Schema migrations run.
  6. The `local_owner` row (uid, vault id, key id) is checked against the vault.

  Only then does a session start.
- **Connections:** `db.connection()` is the only way to the protected database. It takes the key from a per-request
  context variable, set by the access middleware from the session. A request without a session, or a connection
  attempted after lock, gets `DatabaseLocked` (401). There is no unencrypted fallback: `open_encrypted()` refuses
  plaintext, missing or empty files (unless a brand-new installation is being created) and wrong keys.
- **Locking:** lock, idle or absolute expiry, a new unlock and a server restart wipe the session's DEK and database key
  in place (`bytearray` zeroed). A kept reference to the key is then useless. Connections are per request and closed
  at its end. A background sweep ends expired sessions every 5 seconds, so keys don't wait for the next request to be
  wiped.
- **Setup:** setup writes the vault first with the database state `pending`. It then creates the encrypted database
  (all migrations, then the owner row bound to the vault) and marks the vault `encrypted`. A crash in between leaves
  `pending` with a missing or half-created database, which the next unlock completes. Once the state is `encrypted`, a
  missing database is never re-created.
- **Owner identity and audit attribution:** these are unchanged. The owner row stays in the database as the binding and
  the actor record. Schema migration 6 drops only the retired M3.1-A passphrase verifier (the vault has been
  authoritative since M3.1-B).
- **Audit events while locked:** failed unlock or recovery attempts cannot be written to an encrypted database that
  is not open. They are counted in memory and recorded in the next successful unlock's audit entry
  (`failed_attempts_before_unlock`). A server restart forgets the count (see limitations).

## The migration tool: `python -m app.encrypt_migrate`

It is run once per existing plaintext installation. Stop the server first. A running M3.1-C server never opens a
plaintext protected database, so it cannot write to it during the migration. Options:
- `--recovery-key`: unlock with the recovery key instead of the passphrase.
- `--display-name`: needed only for a database that has no owner yet.
- `--rollback`: abandon an interrupted run before activation.
- `--yes`: skip the confirmation prompt.

Secrets are read with getpass, or from standard input when it is not a terminal. They are never taken from arguments
or environment variables.

1. **Checks.** Protected mode; a plaintext SQLite file with a known schema (≤ v5); every case `is_synthetic = 1`.
   Anything else is refused and nothing is changed.
2. **Authentication before any change.** Each kind of installation is handled differently:
   - **M3.1-B** (bound v1 vault): the vault opens with the passphrase or recovery key and is upgraded to v2.
   - **M3.1-A** (owner without a vault): the old verifier checks the passphrase, then a v2 vault is created. Its
     recovery key is shown once on a terminal and confirmed by typing its last group. With no terminal it is never
     printed, and the app shows a fresh one at the first unlock, because an unconfirmed key is always replaced.
   - **A vault left behind by an interrupted M3.1-B initialisation** is adopted, never replaced.
   - **No owner** (pre-M3.1-A): one is created with `--display-name`.
   - **A missing vault for a bound database** is refused, so no new key is ever made for existing data.

   Only the tool's own audited entries are added to the plaintext database: owner setup, vault initialisation or
   upgrade, and `database_encryption_started`. Record rows are never touched.
3. **Source verification.** `PRAGMA integrity_check`, `foreign_key_check`, the audit hash chain, and a **fingerprint**:
   every table's row count and content hash, every schema object (tables, indexes, triggers), `user_version`, and the
   audit head and chain.
4. **Pre-migration backup.** A verified plaintext backup (`backups/oncoscope-pre-encryption-<UTC stamp>/`: database,
   documents, vault, manifest). A re-run reuses it only if it still verifies.
5. **Export.** `sqlcipher_export` into `<database>.encrypting`, keyed with the derived key, followed by `user_version`.
6. **Verification of the encrypted copy:**
   - an identical fingerprint (all tables, schema objects, `user_version`, audit head and chain);
   - `integrity_check`, `cipher_integrity_check` and `foreign_key_check`;
   - a **byte scan** for every text value of the source of 6 bytes or more. Not one may appear, and neither may the
     SQLite header.

   On any difference the copy is deleted and the plaintext database stays in use.
7. **Activation.** The vault records `encrypted` (with the migration time). Then `os.replace` swaps the verified copy
   in, which is atomic on NTFS and ext4. A `database_encrypted` audit entry is appended (migration id, cipher, key
   fingerprint, table and row counts, the source audit head, the backup's name) and the state file is removed.

**The only valid copy is never overwritten.** Until the swap the original is untouched apart from the tool's own audit
entries. The swap replaces it only after a verified backup and a verified encrypted copy both exist.

### Interrupted migrations

Every step updates `encrypt-migration.json` atomically. It is plaintext but holds hashes, counts and paths only, never
record content or secrets. While it exists the server reports `migration_interrupted` and refuses to unlock. Running the
tool again decides from the files on disk alone:

| Interrupted after / during | Database file | What a re-run does |
|---|---|---|
| start, backup, export, verification | plaintext | Re-checks the source, reuses the backup if it still verifies, discards any staging file (also a torn one) and re-exports |
| the vault was marked encrypted, before the swap | plaintext | The same; the vault already says encrypted, which is allowed while a migration record exists |
| **the final replacement** (`os.replace` failed) | plaintext | The same, then swaps |
| the database lost during the swap (non-atomic file system) | missing, staging present | Verifies the staging copy against the recorded fingerprint and activates it; if it does not verify, refuses and points at the plaintext backup |
| after the swap, before or after the audit entry | encrypted | Checks that the active database matches the recorded fingerprint, appends the audit entry if it is not there yet (never twice), clears the state |

`--rollback` abandons a run that has not swapped yet. It sets the vault back to `plaintext` if needed, deletes the
staging file and the state file, and keeps the backup. After the swap rollback is refused: finish the run instead.
A damaged state file stops the tool without changing anything.

## Backups and restores

- **Making a backup of an encrypted database** needs the passphrase or recovery key (`python -m app.backup
  [--recovery-key]`). SQLite's backup API copies between two connections keyed with the same key, so the copy is
  encrypted page by page and a decrypted database is never written. If a copy ever came out unencrypted it would be
  deleted and the backup refused.
- **What a backup holds:** the encrypted database, the vault and the plaintext documents. The manifest records
  `database_encryption` (encrypted, SQLCipher version, the database key's fingerprint) and the vault's fingerprint for
  the database key.
- **Verifying a backup** needs the same credentials. Verification fails, and the backup is never restored, in any of
  these cases:
  - no credentials;
  - wrong credentials;
  - a missing vault, or a vault whose database key doesn't match the manifest;
  - a database copy that is unencrypted, flipped or truncated;
  - a missing or damaged manifest;
  - a partial document store.
- **Restore** (`python -m app.restore <backup> [--target …] [--recovery-key]`) opens the backup's *own* vault with
  the passphrase or recovery key. It works onto a clean folder with either credential, and the server there then
  unlocks normally. A wrong credential fails before anything is written. An existing target is backed up first, and a
  different vault at the target is never replaced (as in M3.1-B).
- **Demonstration mode** backups are unchanged (plain SQLite, no credentials).
- Backup manifests are not authenticated yet, and documents in backups are plaintext (M3.1-D).

## Remaining plaintext storage

Protected-mode storage that is **not** encrypted after M3.1-C:

| Location | What is in it | Notes |
|---|---|---|
| `backend/data/documents/` | Original documents (fictional), named by SHA-256 | **M3.1-D.** Their extracted text is in the database, so the text is encrypted while the original file is not |
| `backend/data/backups/oncoscope-pre-encryption-*` | **A complete plaintext copy** of the old database, its documents and vault | Made by the migration on purpose; retire it yourself (below) |
| Backups made before M3.1-C | Plaintext databases | The same as above |
| Backups made after M3.1-C | Encrypted database, plaintext documents, plaintext `manifest.json` | Manifests hold row counts, per-table content hashes, document hashes and the audit head, never record text. Hashes can confirm a guessed table's content (M3.1-D: HMAC manifests) |
| `oncoscope-vault.json` | Owner display name and uid, vault id, key id, timestamps, the database state and key fingerprint, wrapped keys | Pre-unlock metadata, authenticated by the MAC only at unlock |
| `encrypt-migration.json` (during a migration only) | Hashes, counts, paths, migration id | Removed when the migration finishes |
| Exports and reports you download | JSON, CSV and Markdown in plaintext | By design: wherever you save them |
| Demonstration database (`data/demo/`) | Fictional demonstration records | By design |
| Memory while unlocked | DEK and database key, decrypted pages in SQLite's page cache, Python objects, case data in the browser tab | `cipher_memory_security` is off; Python cannot reliably wipe transient copies |
| The disk's free blocks | Remnants of the old plaintext database file and of deleted files | `os.replace` and deletion do not erase old blocks (SSDs and journalling file systems in particular). Only full-disk encryption covers these |

### Identifying and retiring the plaintext pre-migration backup

You can identify it in three ways:
- **Folder name:** `oncoscope-pre-encryption-<UTC stamp>` in `backend/data/backups/`. The tool prints the full path
  with a warning.
- **Manifest:** its `manifest.json` has `"database_encryption": {"encrypted": false}`.
- **Audit log:** the `database_encrypted` audit entry names it under `plaintext_backup`.

To retire it:
1. Unlock OncoScope and check the case.
2. Make a new encrypted backup: `python -m app.backup <somewhere>`, then copy it and the recovery key off the computer.
3. Delete the `oncoscope-pre-encryption-*` folder, and any older plaintext backups, from every place you copied them.

**Deleting a file does not reliably erase it.** On SSDs, and on file systems with journalling or snapshots, the old
blocks can survive, and overwriting tools cannot guarantee otherwise. OncoScope therefore claims no secure deletion.
Full-disk encryption (BitLocker, FileVault, LUKS) is what protects remnants and copies.

## Integrity checks performed by the migration

- **Source:** `integrity_check` = ok; `foreign_key_check` empty; audit chain valid.
- **Copy:**
  - the same `user_version`;
  - the same set of tables, with every table's row count and SHA-256 of its rows in rowid order;
  - the same schema objects (SHA-256 of every `sqlite_master` row: tables, indexes, triggers including the
    append-only audit triggers);
  - the same audit head (entries, last id, last hash) and a valid chain;
  - `integrity_check` = ok, `cipher_integrity_check` empty, `foreign_key_check` empty.
- **Plaintext scan:** none of the source's text values (≥ 6 bytes) appears in the copy's bytes; there is no SQLite
  header.
- **After the swap:** the active database still matches the recorded table hashes and audit head before the audit
  entry is written.

On the fictional M3.1-B installation used by the browser demonstration (`fixtures/legacy_installation.py`), measured
on 2026-10-27:
- all **12 tables** (33 rows) were identical;
- the scan checked **108 distinct text values**, with none found;
- the source audit head had 18 entries, and the migration added exactly three in total: vault upgrade, migration
  start, migration done.

## Tests

**Backend, `backend/tests/test_encryption.py` — 34 tests:**

| # | Requirement | Tests |
|---|---|---|
| 1 | Ordinary SQLite cannot read it | `test_a_new_protected_database_is_encrypted_and_unreadable_with_ordinary_sqlite`; `test_security.py::test_database_file_does_not_contain_plaintext_records` (formerly a strict xfail) |
| 2 | No record values in database bytes, journals, temp files | `test_record_values_never_appear_in_database_bytes_journals_or_temporary_files` (an open write transaction's journal and a temp table included) |
| 3 | Correct credentials open it | `test_credentials_recovery_and_passphrase_change_keep_the_same_database_key` |
| 4 | Wrong credentials cannot open or modify it | the same test (the bytes are unchanged), `test_the_migration_refuses_unsafe_starting_points`, random and zero keys in test 1 |
| 5 | Recovery key returns the original records | the same test; `test_an_m31b_installation_migrates_without_loss` (an M3.1-B recovery key after migration) |
| 6 | Passphrase change keeps data and database key | `test_credentials_recovery_and_passphrase_change_keep_the_same_database_key`; `test_vault.py::test_changing_the_passphrase` |
| 7 | Plaintext databases migrate without loss | `test_an_m31b_installation_migrates_without_loss` (identical API views: timeline, labs, series, evidence, extractions, audit, history, document text and bytes, report, export); `test_an_m31a_installation_gets_its_vault_during_the_migration`; `test_protected.py::test_existing_v3_database_and_audit_history_survive_protection` |
| 8 | Audit hashes, revisions, evidence links, schema migrations survive | the same three (every pre-existing row identical; the audit log only appended; `schema_migrations` 1–6) |
| 9 | Interrupted migrations are recoverable | `test_an_interrupted_migration_is_completed_by_running_it_again` (8 points, including the final replacement and a torn staging file), `…_can_be_rolled_back_before_activation` (6), `test_a_missing_database_mid_swap_is_completed_from_the_verified_copy`, `test_rollback_is_refused_after_activation_and_a_damaged_record_stops_the_tool` |
| 10 | Missing or corrupted vaults never create a new database over data | `test_missing_or_damaged_files_are_refused_and_never_recreated`, `test_the_migration_refuses_unsafe_starting_points`, `test_vault.py::test_a_missing_vault…`, `…_a_replaced_vault_is_refused` |
| 11 | Backup and restore keep the encrypted database intact | `test_encrypted_backups_stay_encrypted_and_restore_with_either_credential`, `test_damaged_or_partial_encrypted_backups_fail_safely` (6 kinds), `test_the_migration_command_line_with_scripted_input` |
| 12 | No protected access after locking | `test_locking_and_expiry_revoke_database_access`, `test_protected.py::test_protected_writes_without_an_actor_are_refused` |
| 13 | M2–M3.1-B functionality intact | the whole suite, which now runs its protected tests against the encrypted database |

Also covered: `test_an_unavailable_sqlcipher_fails_clearly` and `test_v1_vaults_still_open_and_upgrade_to_v2_without_new_keys`
(in `test_vault.py`).

**Frontend:** `src/encryption.test.tsx` (3 tests) covers the storage notices (migration required, interrupted,
refused) and the normal lock screen. The Security view's encryption facts are checked in `vault.test.tsx`.

**Browser demonstration, `frontend/e2e/encryption.spec.ts` (Playwright project `m3.1c`)** runs the acceptance steps 1–12:
1. A fictional plaintext M3.1-B installation (`fixtures/legacy_installation.py`) has a verified fingerprint and audit
   chain, and ordinary SQLite can read it.
2. The UI refuses to unlock it and explains the migration.
3. `python -m app.backup` makes a backup.
4. `python -m app.encrypt_migrate --yes` migrates it, warning about the plaintext backup.
5. Ordinary SQLite gets "file is not a database", and the file contains none of the case's text.
6. The record tables' hashes are identical to before.
7. Unlock in the browser shows the same case: the corrected and verified lab (revision 4), the extracted lab, the
   timeline event, documents, audit reasons, and the Security view showing "Encrypted".
8. A passphrase change leaves the database key fingerprint unchanged.
9. The M3.1-B recovery key opens it.
10. A new server process on the same folder unlocks and serves the case.
11. An interrupted migration (the process killed after `vault_marked`) on a disposable copy is refused meanwhile, then
    completes.
12. That copy's record hashes are unchanged.

There are no secrets in URLs or browser storage.

## Deviations from the plan

- **Library:** the upstream `sqlcipher3` 0.6.4 (SQLCipher 4.19.0) instead of the PoC's `sqlcipher3-wheels` 0.5.7
  (SQLCipher 4.12.0). It is the maintained package and ships wheels for both CI platforms.
- **Where the pre-unlock metadata lives:** in the vault header (format v2), not in a separate sidecar file.
- **The pre-migration backup:** the plan's "delete the plaintext backup securely and record a tombstone" is not
  implemented. Secure deletion cannot be guaranteed, so the backup is kept, flagged loudly, and retired by the owner.
- **Audit entries in the plaintext database:** the tool appends its own audited entries before taking the backup, so
  the backup and the encrypted copy both contain them.

## Known limitations and risks

1. **Documents are still plaintext** (M3.1-D), as are backup manifests and documents inside backups.
2. **Losing the vault, or both credentials, loses the database.** There is no escrow and no reset. Keep a backup (it
   includes the vault) and the recovery key apart from the computer.
3. **The plaintext pre-migration backup** and any older backups stay readable until the owner retires them. Deleted
   files may survive on disk.
4. **Memory:** keys and decrypted pages live in the server process while unlocked. `cipher_memory_security` is off, and
   Python makes transient copies (including the hex key string for `PRAGMA key`) that cannot be wiped. Same-user
   malware can read them.
5. **Pre-unlock metadata is readable and editable:** the vault header shows the owner's name, and the database file's
   kind is visible. Editing the header can only make the lock screen refuse or mislabel the state. It is detected
   (MAC) at unlock, and it cannot open anything without the key.
6. **Failed unlock attempts are counted in memory only** and are recorded at the next successful unlock. A restart
   loses the count, as it resets the rate limit (M3.1-A). The keyed audit chain and a separate log are M3.1-E.
7. **No rollback protection:** an attacker with write access can swap in an older encrypted database or backup made
   with the same key, or delete files (availability). Audit head receipts are M3.1-E.
8. **One key, no rekey:** a suspected key compromise needs `PRAGMA rekey` plus a new vault (planned for M3.2).
9. **A damaged page in an unlocked session** fails that request with a server error. Unlock itself reports damage as
   409, and SQLCipher's page HMAC means damage is detected, never silently read.
10. **Durability on Windows:** the folder cannot be fsynced after `os.replace`. The migration's state record and
    verified backup cover a lost rename.
11. **The plaintext scan** checks text values of 6 bytes or more. Numbers are stored in binary inside encrypted pages
    and are not scanned as text.
12. **No independent cryptographic or security review** has been done. **Database encryption alone does not make the
    application ready for real patient data.**

## Next increment: M3.1-D

Encrypted document storage and authenticated encrypted backups with the existing vault. Documents get AES-256-GCM under
`derive(dek, "documents")` with HMAC-derived file names (`derive(dek, "document-names")`) and a verified re-encryption
of the existing store, using the same interrupted-run rules as this migration. Backup manifests are authenticated with
`derive(dek, "backup-manifest")`. Backups then contain no plaintext beyond the vault header. See
`M3_1B_KEY_VAULT.md` → "M3.1-D".
