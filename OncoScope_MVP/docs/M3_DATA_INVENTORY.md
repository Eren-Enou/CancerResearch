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
| 1 | `backend/data/oncoscope.sqlite3` — `patients`, `sources`, `events`, `labs`, `treatments`, `studies`, `measurements` | All structured record content, verbatim report text, source labels/notes/origins, document filenames, anchor quotes | Any process running as the same OS user; administrators/root; anyone with the disk (T4) | T1, T2, T4, T12 | Loopback-only API; `.gitignore`; hygiene CI check; foreign keys and validation | Encryption at rest; file permissions tightened to owner-only; authentication on the API |
| 2 | same DB — `audit_log` | Complete before/after rows of every create, correction, status change, attachment, extraction decision; reasons | as #1 | T1, T4, T6, T13 | Append-only triggers; SHA-256 hash chain; head exposed for external recording | Keyed/signed chain or external anchor; actor identity; a deletion policy that coexists with append-only history |
| 3 | same DB — `extractions` | Original text lines, proposed values, issues, decisions | as #1 | as #1 | Review-only; never auto-accepted | as #1 |
| 4 | same DB — `document_text` | **Full extracted text of every attached PDF/TXT** (derived cache) | as #1 | T1, T4, T13 | Keyed by hash + extractor version | Encryption; purge on deletion; reproducible so it could be dropped from backups |
| 5 | SQLite side files (`*.sqlite3-journal`, free pages) | Transient copies of changed pages; overwritten values may persist in free pages | as #1 | T4, T13 | Rollback journal is deleted after each transaction | `PRAGMA secure_delete`; encryption covers these automatically if the whole DB is encrypted |
| 6 | `backend/data/documents/<aa>/<sha256>` | Original document bytes, byte-identical (shared across cases when identical) | as #1 | T1, T2, T4, T7 | SHA-256 verified on every read; immutable once attached; no executable types accepted | Encryption at rest; owner-only permissions; deletion and reference counting |
| 7 | `documents/<aa>/<sha256>.partial` | Document bytes during an atomic write | as #6 | as #6 | Renamed into place immediately | Cleanup of stale partial files after a crash |
| 8 | `backend/data/backups/oncoscope-<UTC>/` (or any `python -m app.backup <dir>` target) | Full DB copy, full document store, `manifest.json` (table hashes, audit head, document hashes) | as #1, plus wherever the user copies it (USB, cloud) | T4, T5 | Manifest verification; restore refuses tampered/unexpected content | **Encryption of backups**; key escrow/recovery; restore-test procedure |
| 9 | `<target dir>/pre-restore-backups/oncoscope-<UTC>/` | Previous database state preserved by every restore | as #8 | T5, T13 | Verified like any backup | Retention limit; encryption; listed in deletion procedures |
| 10 | `<target>.restoring` | Staging copy during restore | as #1 | T4 | Replaced atomically | Cleanup after interrupted restore |
| 11 | OS temp directories | Test/benchmark databases: pytest `tmp_path` (pytest keeps the last 3 runs), Playwright e2e databases `oncoscope-e2e-*`, e2e backup/report/restore folders `oncoscope-backup-*`, `oncoscope-report-*`, `oncoscope-restored-*` | Same OS user | T4 (fictional only today) | `app.benchmark` now deletes its temp DB | Clean up e2e temp folders; never run tests against real data |
| 12 | Browser download folder | Exported `oncoscope-synthetic-<id>.json`, `*.csv`, saved `report.md`, opened original documents | Same OS user; cloud-synced folders | T4, T8 | Export refused for non-synthetic cases; CSV formula guard | Encrypted export option; export audit entry; warning on export |
| 13 | Playwright `frontend/test-results/` | Traces on failure: DOM snapshots, network bodies, screenshots | Same OS user | T8 | Git-ignored; never uploaded by CI | Delete after use |
| 14 | `docs/screenshots/*.png` (committed) | Screenshots of fictional cases | **Public** (repository is public) | T16 | Fictional data only; hygiene check limits images to this folder | Policy: screenshots only from fixtures |
| 15 | Repository files (public on GitHub) | Code, fictional fixtures; `.claude/launch.json` contains a local Windows user path | **Public** | T9, T16 | Hygiene check (databases, stores, backups, secrets, unlabelled fixtures) | Remove local paths from tracked config; keep repository free of any real data permanently |

## 2. In memory and in transit

| # | Location | Content | Readers | Threats | Existing protection | Missing controls |
|---|---|---|---|---|---|---|
| 16 | FastAPI/uvicorn process memory | Request/response bodies, document bytes, extracted text during parsing | Same-user processes with debug rights; crash dumps | T1, T7 | Loopback bind; Host-header check | Parsing isolation (separate low-privilege process); crash-dump policy |
| 17 | pypdf parsing (in the API process) | Untrusted PDF structures | — | T7 | Upload type/magic checks; 10 MB cap; pypdf decompression limit (a 400 MB bomb is refused in < 1 s; a 40 MB stream parses in ~3.6 s / ~90 MB peak) | Run parsing in a sandboxed subprocess with time/memory limits |
| 18 | HTTP between browser, Vite dev proxy and API (`127.0.0.1`) | All API traffic, unencrypted on loopback | Same-machine processes able to sniff loopback (admin/root) | T1, T3 | Loopback only; `Cache-Control: no-store`; no CORS grants; JSON-only mutations | Session token per launch (authentication); TLS is optional on loopback |
| 19 | Browser memory (React state, pdf.js worker, fetched document ArrayBuffers) | The whole open case and documents | Browser extensions with page access; same-user malware | T1, T3 | No `localStorage`/IndexedDB use; URL hash holds only case id and view name | Clear state on lock/logout; extension guidance |
| 20 | Browser HTTP cache | API responses | Same OS user | T4, T8 | **Fixed in M3.0:** `Cache-Control: no-store` on all `/api` responses | — |
| 21 | Browser history | URLs only: `/api/patients/<id>/report.md`, `#case=<id>&view=…` (ids, no content). Before M3.0, uploads put the **filename** in the URL | Same OS user | T8 | **Fixed in M3.0:** filename moved to `X-Document-Filename` header | — |
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
