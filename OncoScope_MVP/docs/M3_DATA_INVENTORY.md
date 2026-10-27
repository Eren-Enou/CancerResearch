# M3 sensitive-data inventory (prepared with synthetic data only)

Purpose: list **every place OncoScope can store, transmit, cache or export record content**, who/what can read it, the
threats that apply (IDs refer to `M3_THREAT_MODEL.md`), what protects it today, and what is missing. "Record content"
means anything describing a case: labels, values, dates, report text, document bytes, extracted text, filenames,
reasons, audit details. Today all such content is fictional; this inventory describes what *would* be sensitive.

Paths are relative to `OncoScope_MVP/` unless absolute. Default data root: `backend/data/` (override with
`ONCOSCOPE_DB_PATH`; documents default to `<db dir>/documents`, override with `ONCOSCOPE_DOCUMENT_DIR`).

Legend — **Readers**: who/what can read it on a typical single-user Windows/macOS/Linux machine.

## 1. At rest on the local disk

| # | Location | Content | Readers | Threats | Existing protection | Missing controls |
|---|---|---|---|---|---|---|
| 1 | `backend/data/oncoscope.sqlite3` — `patients`, `sources`, `events`, `labs`, `treatments`, `studies`, `measurements` | All structured record content, verbatim report text, source labels/notes/origins, document filenames, anchor quotes | Protected mode (M3.1-C): **SQLCipher-encrypted**, readable only with the vault-derived key inside an unlocked session; demonstration mode: plain SQLite (fictional) | T1, T2, T4, T12 | Loopback-only API; `.gitignore`; hygiene CI check; validation; authentication (M3.1-A); **SQLCipher 4 encryption of the whole file incl. journals (M3.1-C)** | File permissions tightened to owner-only (M3.1-F); rekey procedure (M3.2) |
| 2 | same DB — `audit_log` | Complete before/after rows of every create, correction, status change, attachment, extraction decision; reasons | as #1 | T1, T4, T6, T13 | Append-only triggers; SHA-256 hash chain; head exposed for external recording | Keyed/signed chain or external anchor; actor identity; a deletion policy that coexists with append-only history |
| 3 | same DB — `extractions` | Original text lines, proposed values, issues, decisions | as #1 | as #1 | Review-only; never auto-accepted | as #1 |
| 4 | same DB — `document_text` | **Full extracted text of every attached PDF/TXT** (derived cache) | as #1 | T1, T4, T13 | Keyed by hash + extractor version | Encryption; purge on deletion; reproducible so it could be dropped from backups |
| 5 | SQLite side files (`*.sqlite3-journal`, free pages) | Transient copies of changed pages; overwritten values may persist in free pages | as #1 | T4, T13 | Rollback journal is deleted after each transaction | `PRAGMA secure_delete`; encryption covers these automatically if the whole DB is encrypted |
| 6 | `backend/data/documents/<aa>/<sha256>` | Original document bytes, byte-identical (shared across cases when identical) | as #1 | T1, T2, T4, T7 | SHA-256 verified on every read; immutable once attached; no executable types accepted | Encryption at rest; owner-only permissions; deletion and reference counting |
| 7 | `documents/<aa>/<sha256>.partial` | Document bytes during an atomic write | as #6 | as #6 | Renamed into place immediately | Cleanup of stale partial files after a crash |
| 8 | `backend/data/backups/oncoscope-<UTC>/` (or any `python -m app.backup <dir>` target) | Full DB copy (**encrypted** in protected mode since M3.1-C), full document store (plaintext), vault, `manifest.json` (table hashes, audit head, document hashes, key fingerprint) | as #1, plus wherever the user copies it (USB, cloud) | T4, T5 | Manifest verification; restore refuses tampered/unexpected content; making, verifying and restoring an encrypted backup needs the passphrase or recovery key | Encrypted documents and an authenticated manifest (M3.1-D); restore-test procedure |
| 8a | `backend/data/backups/oncoscope-pre-encryption-<UTC>/` (M3.1-C migration) | **A complete plaintext copy** of the pre-migration database, documents and vault | as #8 | T4, T5 | Verified; named and flagged by the tool and in the `database_encrypted` audit entry | The owner retires it after checking the encrypted installation; no secure deletion is claimed |
| 8b | `backend/data/encrypt-migration.json` (only during a migration) | Migration id, step, table counts and content hashes, audit head, backup path | as #1 | T13 | No record content or secrets; removed on completion | — |
| 9 | `<target dir>/pre-restore-backups/oncoscope-<UTC>/` | Previous database state preserved by every restore | as #8 | T5, T13 | Verified like any backup | Retention limit; encryption; listed in deletion procedures |
| 10 | `<target>.restoring` | Staging copy during restore | as #1 | T4 | Replaced atomically | Cleanup after interrupted restore |
| 11 | OS temp directories | Test/benchmark databases: pytest `tmp_path` (pytest keeps the last 3 runs), Playwright e2e databases `oncoscope-e2e-*`, e2e backup/report/restore folders `oncoscope-backup-*`, `oncoscope-report-*`, `oncoscope-restored-*` | Same OS user | T4 (fictional only today) | `app.benchmark` now deletes its temp DB | Clean up e2e temp folders; never run tests against real data |
| 12 | Browser download folder | Exported `oncoscope-synthetic-<id>.json`, `*.csv`, saved `report.md`, opened original documents | Same OS user; cloud-synced folders | T4, T8 | Export refused for non-synthetic cases; CSV formula guard | Encrypted export option; export audit entry; warning on export |
| 13 | Playwright `frontend/test-results/` | Traces on failure: DOM snapshots, network bodies, screenshots | Same OS user | T8 | Git-ignored; never uploaded by CI | Delete after use |
| 14 | `docs/screenshots/*.png` (committed) | Screenshots of fictional cases | **Public** (repository is public) | T16 | Fictional data only; hygiene check limits images to this folder | Policy: screenshots only from fixtures |
| 15 | Repository files (public on GitHub) | Code, fictional fixtures; `.claude/launch.json` contains a local Windows user path | **Public** | T9, T16 | Hygiene check (databases, stores, backups, secrets, unlabelled fixtures) | Remove local paths from tracked config; keep repository free of any real data permanently |

| 15a | same DB — `local_owner` (schema v4) | Owner display name, uid, vault binding; the Argon2id verifier was **dropped by migration 6** (M3.1-C) | as #1 | T1, T4 | One row, cannot be deleted; encrypted with the DB | — |
| 15c | `backend/data/oncoscope-vault.json` (M3.1-B; also copied into every backup) | Vault id, owner uid, key id, Argon2id/HKDF parameters and salts, AES-GCM-wrapped DEK (twice), MAC; since v2 (M3.1-C) also the **owner display name** and the database state and key fingerprint; **no plaintext key, passphrase or recovery key** | as #1 | T1, T4 (offline passphrase guessing), T5 | AES-256-GCM wraps; Argon2id 64 MiB; MAC + checksum; atomic verified writes; restore never replaces it | Owner-only permissions (M3.1-F); stronger KDF parameters option |
| 15b | same DB — `audit_log.actor_id/actor_label` | Who made each change since v4 (owner uid and name, or `demo`) | as #1 | T6, T12 | Covered by the entry hash | Keyed chain (M3.1-E) |

## 2. In memory and in transit

| # | Location | Content | Readers | Threats | Existing protection | Missing controls |
|---|---|---|---|---|---|---|
| 16 | FastAPI/uvicorn process memory | Request/response bodies, document bytes, extracted text during parsing | Same-user processes with debug rights; crash dumps | T1, T7 | Loopback bind; Host-header check | Parsing isolation (separate low-privilege process); crash-dump policy |
| 17 | pypdf parsing (in the API process) | Untrusted PDF structures | — | T7 | Upload type/magic checks; 10 MB cap; pypdf decompression limit (a 400 MB bomb is refused in < 1 s; a 40 MB stream parses in ~3.6 s / ~90 MB peak) | Run parsing in a sandboxed subprocess with time/memory limits |
| 18 | HTTP between browser, Vite dev proxy and API (`127.0.0.1`) | All API traffic, unencrypted on loopback | Same-machine processes able to sniff loopback (admin/root) | T1, T3 | Loopback only; `Cache-Control: no-store`; no CORS grants; JSON-only mutations | Session token per launch (authentication); TLS is optional on loopback |
| 19 | Browser memory (React state, pdf.js worker, fetched document ArrayBuffers) | The whole open case and documents | Browser extensions with page access; same-user malware | T1, T3 | No `localStorage`/IndexedDB use; URL hash holds only case id and view name; **M3.1-A: workspace unmounted (state discarded) on lock, inactivity, session end and 401** | Extension guidance; JS cannot guarantee memory is wiped |
| 20 | Browser HTTP cache | API responses | Same OS user | T4, T8 | **Fixed in M3.0:** `Cache-Control: no-store` on all `/api` responses | — |
| 21 | Browser history | URLs only: `/api/patients/<id>/report.md`, `#case=<id>&view=…` (ids, no content). Before M3.0, uploads put the **filename** in the URL | Same OS user | T8 | **Fixed in M3.0:** filename moved to `X-Document-Filename` header | — |
| 21a | Session tokens (M3.1-A) | Bearer token: server memory (keyed by its SHA-256), browser JS memory (`api.ts`), `Authorization` header on loopback HTTP | Same-user processes with debug rights; admin able to sniff loopback | T1, T3 | Never in URLs, storage, cookies or logs (log test); 15 min idle / 8 h max; revoked on lock/restart; foreign origins refused | TLS on loopback is optional; OS-level process isolation |
| 21b | Passphrase at unlock/setup | Request body on loopback HTTP; transient in server memory during Argon2id | as 21a | T1 | Not logged; validation errors never echo it; only the verifier is stored | as 21a |
| 21d | DEK and recovery key in memory (M3.1-B) | DEK in the server session (`bytearray`, zeroed on lock/expiry/restart); recovery key in the setup/unlock/recover response body and in React state until confirmed; optionally the clipboard | Same-user malware; clipboard managers; anyone seeing the screen | T1 | Never logged, stored or put in URLs (tests); shown once; UI tells the owner to clear the clipboard | Python cannot guarantee wiping transient copies |
| 21c | Downloads and opened documents (M3.1-A) | Short-lived `blob:` URLs of authorized responses | Same OS user | T8 | Revoked after use / on unmount; unexpected media types never rendered | — |
| 22 | Clipboard / print | Whatever the user copies or prints | Other apps; printers | T8 | — | User guidance |

## 3. Logs, diagnostics and CI

| # | Location | Content | Readers | Threats | Existing protection | Missing controls |
|---|---|---|---|---|---|---|
| 23 | uvicorn stdout/stderr (terminal, Playwright `[WebServer]` output) | Method, path, query, status. **Before M3.0 the attached filename appeared here.** Tracebacks on unexpected errors | Same OS user; anyone who receives a pasted log | T8 | **Fixed:** filename out of URL; tampered references now 409 not 500+traceback; validation errors never echo input; regression test runs the real server and searches its output for sentinel values | Structured logging with an allow-list; log retention |
| 24 | Vite dev server output | Module paths, proxy errors | Same OS user | T8 | No request bodies logged | — |
| 25 | GitHub Actions logs | Test output and server logs from CI runs | **Public** (public repository) | T16 | Fictional fixtures only; no artifact uploads; read-only token | Never configure CI with real data or secrets |
| 26 | Error messages in API responses and UI | Field names and messages; record ids | Same as API caller | T8 | No input echo | — |

## 4. Explicitly not present today
No telemetry, analytics, external AI calls, remote storage, outbound network code (enforced by a test), cookies,
`localStorage`, service workers, or cloud sync performed by the application. (A user's own folder sync, e.g. OneDrive,
can still copy `backend/data/` — see T4/T5.)
