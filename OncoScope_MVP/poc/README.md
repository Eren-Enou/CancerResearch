# Proofs of concept (not used by the application)

`m3_encryption_poc.py` — checks the M3 storage design on fictional data: SQLCipher with a random key, wrong-key refusal,
encrypted backups via the SQLite backup API, `sqlcipher_export` migration of a plaintext OncoScope v3 database, Argon2id
key wrapping and AES-256-GCM tamper detection. Run it in a throwaway virtualenv; the project does not depend on these
packages.

```bash
python -m venv .poc && .poc/Scripts/pip install sqlcipher3-wheels==0.5.7 cryptography==50.0.2 argon2-cffi==25.1.0
.poc/Scripts/python poc/m3_encryption_poc.py <plaintext fictional oncoscope.sqlite3>
```

Result on 2026-10-26 (Windows 11 x64, Python 3.12, SQLCipher 4.12.0 community), against a fictional v3 database:
`plaintext_absent_from_file`, `wrong_key_refused`, `backup_encrypted`, `backup_readable_with_key`, `migration_identical`
(11 tables), `migration_user_version`, 2 triggers kept, `migrated_file_has_no_case_label`, `dek_unwraps`,
`document_tamper_detected` — all true.

Finding: with `PRAGMA cipher_memory_security = ON`, a wrong key crashed the process (stack overflow, exit 127) instead of
raising an error; with it off, a wrong key raises `DatabaseError: file is not a database`.
