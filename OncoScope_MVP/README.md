# OncoScope — Phase 1 local prototype (M3.1-C: encrypted database; synthetic data only)

**Status:** Source-linked medical-record organizer built with synthetic data only. Not a medical device or an oncology decision-support tool.

**Core design goal:** Given two sets of entered records, reproduce their chronology and documented differences, always pointing back to the recorded sources. Never infer stage, efficacy, prognosis, or treatment recommendations.

This repository is intentionally narrow in scope: a **FastAPI + SQLite/SQLCipher API and a React + TypeScript dashboard**. It does not contain medical scans, a real patient's information, machine learning, RECIST response logic, or molecular simulation.

## What's implemented

**M3.1-C — encrypted database and lossless migration** (`docs/M3_1C_DATABASE_ENCRYPTION.md`).
- **Encrypted database:** in protected mode the database is a SQLCipher 4 file (sqlcipher3 0.6.4, SQLCipher 4.19.0),
  keyed with a key derived from the vault's data key. Ordinary SQLite cannot read it, and its bytes, journals and
  temporary files contain no record text.
- **Locked start:** the server starts locked and reads nothing protected before unlock. Unlocking opens the vault
  first, so a wrong credential never reaches SQLCipher. Locking wipes the key.
- **Migration tool:** `python -m app.encrypt_migrate` encrypts an existing plaintext (fictional) installation:
  - verified source and backup;
  - `sqlcipher_export`;
  - an identical fingerprint of every table, schema object and the audit head;
  - a byte scan for plaintext;
  - an atomic swap.

  An interrupted run finishes or rolls back when the tool is run again.
- **Backups:** encrypted backups stay encrypted and restore onto a clean folder with the passphrase or the recovery
  key.
- **Still plaintext:** original documents, backup manifests and the migration's own pre-migration backup (M3.1-D;
  retiring that backup is described in the doc).

**M3.1-B — key vault and recovery** (`docs/M3_1B_KEY_VAULT.md`). A protected installation has a local key vault: a
random 256-bit data key wrapped with AES-256-GCM under an Argon2id key from the passphrase and, separately, under a
recovery key shown once at setup. Recover with the recovery key, change the passphrase, and the data key stays the
same; damaged, missing, replaced or half-written vaults are refused, never regenerated. (M3.1-C now derives the
database key from it.)

**M3.1-A — protected local access and audit identity** (`docs/M3_1A_PROTECTED_ACCESS.md`). (Records were still
plaintext on disk until M3.1-C.)
- Protected mode (the default): first launch creates a single local owner with a passphrase (Argon2id verifier via
  `argon2-cffi`); unlocking opens a short-lived in-memory session (bearer token, 15 min idle / 8 h maximum, one at a time).
  Every API route except health, auth status, setup and unlock answers 401 without it. Lock revokes it at once.
- Wrong passphrases are rate limited (429 with a growing delay); foreign web origins and non-loopback peers get 403.
- Every new audit entry records the authenticated owner; entries from before schema v4 stay "unknown".
- Lock screen; the UI locks itself on inactivity, at the end of the session lifetime and on any 401, and discards all
  case data it held. Documents, PDF pages, images, reports and exports are fetched with the session header (no tokens in
  URLs, storage or cookies).
- Demonstration mode (`ONCOSCOPE_MODE=demo`) stays available for fictional demos and tests, uses its own database and
  refuses to open a database that has a local owner.

**M3.0 — secure foundations and release reliability.** This does **not** make OncoScope safe for real patient records.
- GitHub Actions CI (`.github/workflows/ci.yml`): repository hygiene (no databases, document stores, backups, documents
  outside fictional fixtures, unlabelled fixtures or secrets), backend tests, main and held-out benchmark gates,
  TypeScript build and unit tests, and the three Playwright demonstrations on Chromium. No artifacts are uploaded.
- Sensitive-data inventory (`docs/M3_DATA_INVENTORY.md`), threat model (`docs/M3_THREAT_MODEL.md`), proposed security
  architecture (`docs/M3_SECURITY_ARCHITECTURE.md`) and the M3.1 plan (`docs/M3_1_IMPLEMENTATION_PLAN.md`); integration
  order for the open PRs (`docs/M3_INTEGRATION_PLAN.md`).
- Security regression suite (`backend/tests/test_security.py`) and fixes for five confirmed defects: document filenames in
  access logs, cacheable API responses, a 500 on tampered document references, restores copying arbitrary files into the
  document store, and a leftover benchmark database. Known gaps (no authentication, plaintext at rest, audit tail
  truncation without an external head) are strict expected failures.
- Encryption proof of concept on fictional data (`poc/`, not used by the app).
- Plain-language labels, a glossary, and WCAG AA text contrast enforced by a test.

**M2.6 — document fidelity and reliability** (plan: `docs/M2_6_PLAN.md`, benchmark: `docs/benchmark/README.md`)

- **Rendered PDF pages** with pdf.js (legacy build): zoom, page navigation, scrolling; the browser re-hashes the file
  and refuses to render on a SHA-256 mismatch. The exact text-layer view is one tab away.
- **Two kinds of location, never conflated**: text-layer citations (page + character offsets, what the report cites)
  and rendered-page regions (page + x,y,w,h %). A citation is highlighted on the rendered page only if its quote matches
  exactly one place there; otherwise the viewer says it could not be placed.
- **Keyboard-accessible region selection** (arrows move, Shift+arrows resize, numeric inputs) on PDF pages and images —
  the manual transcription path for documents without a text layer
- **Benchmark corpus**: 26 fictional documents (text, PDF tables, multi-page, two-column, text-free PDF, image,
  radiology) with a hand-written ground-truth manifest, plus 8 held-out documents written after the parser was frozen
- **Parser `ca125-lab` v2.1**: layout-tolerant rules (label variants, tables, flags, reference ranges, row dates,
  collection vs report dates, unit before value, value on next line); flags multiple values, dotted thousands
  separators, mentions without results; never duplicates proposals across parser versions
- **Evaluation tool** `python -m app.benchmark [--holdout]`: precision, recall, field and anchor accuracy, ambiguity
  kept visible, duplicates, findings, and every non-ideal outcome by layout; gate = zero silently incorrect results
- **Case history view**: date picker with everything recorded that day, supporting documents, pending proposals and
  conflicts (never resolved); CA-125 chart with treatment dates and unreviewed markers; every value links to evidence
- **One-step document import** (duplicates refused before anything is created); case and view kept across reloads

**M2.5 — reconstructing a case from documents** (walkthrough: `docs/WALKTHROUGH_M2_5.md`)

**M2.5 — reconstructing a case from documents** (walkthrough: `docs/WALKTHROUGH_M2_5.md`)

- **Documents view**: each attached fictional file shown beside the records that cite it — the deterministic text layer
  (UTF-8 text; pypdf for PDFs, per page) with cited passages highlighted, or the image with cited regions outlined; the
  original file stays byte-identical and is re-hashed whenever it is served
- **Evidence anchors**: any observation can cite a page + exact quoted passage (resolved to character offsets and checked
  against the document text; absent or ambiguous quotes are refused) or an image region `x,y,w,h` (%)
- **Provenance** on every observation: `transcribed` (from a document), `extracted` (accepted from the review queue) or
  `user_observation` (not from a document; cannot carry an anchor)
- **Source-anchored transcription**: select text (or drag a region) in the viewer → the entry form opens prefilled with the
  source and location
- **Deterministic CA-125 extraction** (`ca125-lab` v1): proposes test/value/unit/date per line with the original passage and
  location; reports `date_ambiguous`, `value_qualifier`, `value_not_found`, `unit_not_accepted`, `date_after_source` … instead
  of guessing; re-running never duplicates proposals
- **Review queue**: compare each proposal with its original text; accept, correct (reason required) or reject (reason
  required); accepted values become `extracted` records in state `reviewed`, never `verified`; all decisions audited
- **Evidence view**: conflicting values, possible duplicate reports / same events, observations dated after their source,
  transcriptions without a location, sources without a file, pending proposals — listed, never resolved; records per
  date and per document; consecutive same-unit differences
- **Traceable report**: every observation line names provenance, file, SHA-256 prefix, page and character range with the
  quoted passage; `fixtures/audit_report.py` re-checks those citations against the original files without the app
- **Verified backup/restore**: backups carry a manifest (table hashes, audit chain, document hashes); `python -m app.restore`
  verifies before and after, preserves any existing target, and refuses tampered backups
- Duplicate imports (same bytes within a case) refused; schema migration 3 upgrades M2 databases in place

**M2 — hardening** (still in place)

- Fictional demonstration case seeded into a local SQLite database; repeatable seeding
- **Blank-database start in the UI**: create a fictional case (always flagged synthetic; the API has no way to create a non-synthetic case)
- Source registry and source-linked clinical events, laboratory results, treatments, imaging report metadata, and measurements
- **Synthetic source documents**: attach a PDF/TXT/PNG/JPEG (≤ 10 MB, magic-byte checked) to a source; SHA-256 recorded, file stored content-addressed, immutable once attached, re-hashed on every read (`/verify` endpoint and "Verify hash" button)
- **Audited correction of every record type** (`PATCH /api/records/{type}/{id}`): mandatory reason, full re-validation of the merged record, revision counter, original values kept in the audit log; per-record history in the UI
- **Verification states** `entered` → `reviewed` → `verified`, audited; any later correction resets the record to `entered`
- **Append-only, hash-chained audit log** (SQLite triggers refuse UPDATE/DELETE; `GET /api/audit/verify` recomputes the chain)
- **Unit rules**: values are never converted; CA-125 accepts only the exact spellings `U/mL` or `kU/L` and no negative values; a unit differing from an existing one only by letter case is refused; each unit is its own chart series and results in other units are listed under the chart, not hidden
- **Measurement rules**: 0 < long axis ≤ 1000 mm, short axis ≤ long axis, lesion keys without spaces; comparison refuses a "later" study dated before the "earlier" one and flags site changes under one key; differences are computed in decimal (41.3 → 35.1 is exactly −6.2)
- Chronological merged timeline, now including lesion measurements (dated by their study)
- Markdown report: every line cites `[record #id; state]` and `[source #id: label]`, marks corrected revisions, ends with a source register (with document hashes and citation counts) and the audit-chain status
- **Read-only export** of synthetic cases: full JSON (records + audit log, no document bytes) and per-table CSV with a spreadsheet formula-injection guard; non-synthetic cases are refused (403)
- **Versioned SQLite migrations** (`PRAGMA user_version` + `schema_migrations`); an unversioned Phase 1 database is upgraded in place
- Non-destructive local backup: `python -m app.backup` (an encrypted database needs the passphrase or `--recovery-key`)
- Loopback-only Host header check (DNS-rebinding protection); validation errors never echo submitted values
- No external API requests, remote database, embedded AI, or telemetry

## Quick start (Windows PowerShell)

Requires **Python 3.11+** and **Node.js 20+** with npm. Use two terminals.

**Terminal 1 — backend**

```powershell
cd OncoScope_MVP\backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

This starts **protected mode** (`backend/data/oncoscope.sqlite3`): the browser asks you to create the local owner on
first launch, then to unlock. To run the unauthenticated **demonstration mode** on its own database instead:

```powershell
$env:ONCOSCOPE_MODE = "demo"
python -m app.seed        # optional: fictional demo case (demonstration mode only)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

If activation is blocked by execution policy, run `.\.venv\Scripts\python.exe` in place of `python` without activating. The API documentation is at http://127.0.0.1:8000/docs .

**Terminal 2 — frontend**

```powershell
cd OncoScope_MVP\frontend
npm install
npm run dev
```

Open **http://127.0.0.1:5173**. The Vite local proxy forwards `/api` requests to the FastAPI process on the same machine.

### macOS/Linux equivalents

```bash
cd OncoScope_MVP/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000          # protected mode
# or: ONCOSCOPE_MODE=demo python -m app.seed && ONCOSCOPE_MODE=demo uvicorn app.main:app --host 127.0.0.1 --port 8000
# separate terminal:
cd OncoScope_MVP/frontend
npm install && npm run dev
```

### Run tests

```powershell
cd OncoScope_MVP\backend
python -m pytest -q                 # 208 passed + 1 expected failure (audit tail truncation, M3.1-E)
python -m app.benchmark             # extraction benchmark against the fictional ground truth (add --holdout for the held-out set)

cd ..\frontend
npm run build                       # strict TypeScript + production build
npm test                            # Vitest component/unit tests (48, incl. WCAG contrast, lock, recovery and storage screens)
npm run test:e2e                    # Playwright: M2, M2.5, M2.6 (demonstration mode), M3.1-A, M3.1-B and M3.1-C (protected mode)
```

`npm run test:e2e` starts six isolated stacks (API 8010–8015 + Vite 5180–5185 for M2, M2.5, M2.6 in demonstration mode, M3.1-A in protected mode with a 45-second session lifetime, and M3.1-B and M3.1-C in protected mode), each on a **new empty database** in the OS temp directory, then drives the UI. The M3.1-C run builds a fictional plaintext installation, migrates it with the CLI, and starts a second server process to check a restart. The M2.6 run saves screenshots to `docs/screenshots/`. The M2.5 run also calls the backup/restore CLI and the report audit script. It uses the installed Microsoft Edge (`PW_CHANNEL=chrome` to use Chrome instead; on non-Windows set `ONCOSCOPE_PYTHON` if the venv is elsewhere). No browser download is needed.

## User workflow

For the document-centred M2.5 workflow follow `docs/WALKTHROUGH_M2_5.md`. The general workflow:

1. On an empty database, create a **fictional case**. Otherwise select **Overview**.
2. **Add record** → create a **Source** first (label, type, date, fictional origin). The new source is preselected for the next record; date, test, unit and study also stay filled for fast entry from one document.
3. Enter labs, treatments, events, imaging studies and lesion measurements. Choosing a study for a measurement preselects that study's source.
4. In **Sources**, attach the synthetic original file; its SHA-256 is shown and can be re-verified.
5. Use **Correct** on any row (Timeline, Laboratory, Imaging, Sources) to fix a transcription error with a reason; **History** shows original → corrected values. **Audit** lists all changes and the hash-chain status.
6. **Imaging** compares recorded long axes by lesion key; changes are arithmetic only, not response categories.
7. **Markdown report** / **Export JSON** (top right) and the CSV links under **Add record** export the synthetic case.

## API overview

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Local API health, schema version and mode (no session needed) |
| GET | `/api/auth/status` | Mode, whether owner setup is needed, and the storage state (`setup`, `locked`, `migration_required`, `migration_interrupted`, `refused`) decided from the files on disk (no session needed) |
| POST | `/api/auth/setup` / `/api/auth/unlock` | Create the local owner (once) / unlock; both return a session token |
| POST | `/api/auth/lock` | Revoke the current session (and drop its data key from memory) |
| POST | `/api/auth/recover` | Recovery key + new passphrase → same data key, new session (no session needed; rate limited) |
| GET | `/api/vault/status` | Vault identifiers, KDF settings, database encryption (SQLCipher version, key fingerprint), derived-key fingerprints (never keys) |
| POST | `/api/vault/passphrase` / `/api/vault/recovery-key/confirm` | Change the passphrase / confirm the recovery key was saved |
| GET / POST | `/api/patients` | List / create fictional cases (always synthetic) |
| GET | `/api/patients/{id}/dashboard` | Patient metadata and counts |
| GET / POST | `/api/patients/{id}/sources` / `/api/sources` | Register source documents |
| PUT / GET | `/api/sources/{id}/document` | Attach (once; filename in the `X-Document-Filename` header, URL-encoded) / open a synthetic source file |
| GET | `/api/sources/{id}/document/verify` | Re-hash the stored file |
| GET / POST | `/api/patients/{id}/events` / `/api/events` | Clinical event metadata |
| GET / POST | `/api/patients/{id}/labs` / `/api/labs` | Laboratory observations |
| GET | `/api/patients/{id}/labs/series?test=CA-125[&unit=]` | One exact-unit series plus excluded other-unit results |
| GET / POST | `/api/patients/{id}/treatments` / `/api/treatments` | Therapy administrations |
| GET / POST | `/api/patients/{id}/studies` / `/api/studies` | Imaging study and report metadata |
| GET / POST | `/api/patients/{id}/measurements` / `/api/measurements` | Reported lesion dimensions |
| GET / PATCH | `/api/records/{type}/{id}` | Read / correct any record (`{"changes": {...}, "reason": "..."}`) |
| POST | `/api/records/{type}/{id}/status` | Set `entered` / `reviewed` / `verified` with a reason |
| GET | `/api/records/{type}/{id}/history` | Audit entries for one record |
| PATCH | `/api/labs/{id}` | Phase 1 lab correction (kept; routed through the generic correction) |
| GET | `/api/patients/{id}/timeline` | Unified chronological record |
| GET | `/api/patients/{id}/compare?baseline_id=1&followup_id=2` | Source-linked arithmetic differences |
| GET | `/api/patients/{id}/audit` · `/api/audit/verify` | Audit history · hash-chain verification |
| GET | `/api/sources/{id}/document/text` | Deterministic text layer used for anchors and extraction |
| POST | `/api/sources/{id}/extract` | Propose CA-125 transcriptions (idempotent) |
| GET | `/api/patients/{id}/extractions[?status=]` | Review queue |
| POST | `/api/extractions/{id}/accept` · `/reject` | Audited decision (`{"changes": {...}, "reason": "..."}`) |
| GET | `/api/patients/{id}/evidence` | Findings: conflicts, duplicates, date mismatches, missing support |
| GET | `/api/patients/{id}/report.md` | Descriptive Markdown report with evidence citations |
| GET | `/api/patients/{id}/export.json` · `/export/{table}.csv` | Read-only synthetic export |

`{type}` is one of `sources`, `events`, `labs`, `treatments`, `studies`, `measurements`. Clinical dates are ISO `YYYY-MM-DD` calendar dates (observations cannot be dated in the future; `plan` events can). System timestamps (`entered_at`, `updated_at`, audit `recorded_at`) are UTC with a `Z` suffix; rows created before migration 2 keep SQLite's `YYYY-MM-DD HH:MM:SS` UTC form. The app performs **no unit conversion**.

## Repository map

```text
OncoScope_MVP/
├── backend/
│   ├── app/
│   │   ├── main.py          FastAPI routes, corrections, comparisons
│   │   ├── db.py            SQLCipher/SQLite connections, migrations, transactions, hash-chained audit writes
│   │   ├── auth.py / vault.py  Local owner, sessions, key vault (v2), unlock order
│   │   ├── encrypt_migrate.py  Plaintext → SQLCipher migration CLI (verified, resumable)
│   │   ├── credentials.py   Passphrase / recovery-key prompts for the CLI tools
│   │   ├── models.py        Pydantic request validation (dates, single-line fields, measurement rules)
│   │   ├── units.py         Lab unit registry, number formatting, exact differences
│   │   ├── documents.py     Content-addressed synthetic document store (SHA-256)
│   │   ├── reporting.py     Markdown report, JSON/CSV export
│   │   ├── extraction.py    Rule-based CA-125 parser (proposals only)
│   │   ├── evidence.py      Documentation findings (never resolved automatically)
│   │   ├── backup.py        Backup CLI with verification manifest
│   │   ├── restore.py       Verified, non-destructive restore CLI
│   │   └── seed.py          Explicitly synthetic ovarian-monitoring example
│   ├── tests/test_api.py    Phase 1 API regression tests
│   ├── tests/test_m2.py     M2 tests incl. the required demonstration from a blank database
│   ├── tests/test_m25_*.py  Evidence anchors, extraction, findings/report audit, backup/restore
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── src/App.tsx          Views and data loading
│   ├── src/components/      Chart, correction/history tools, forms, sources, audit
│   ├── src/records.ts       Editable-field specs, change detection, number display
│   ├── src/*.test.ts(x)     Vitest tests
│   ├── e2e/                 Playwright M2 and M2.5 demonstrations
│   └── src/styles.css       Responsive visual design
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SECURITY.md
│   ├── ROADMAP.md
│   ├── M2_PLAN.md           M2 findings and milestone plan
│   ├── M2_5_PLAN.md         M2.5 milestones and acceptance criteria
│   └── WALKTHROUGH_M2_5.md  Step-by-step case reconstruction demonstration
├── fixtures/
│   ├── fictional/           Fictional lab/CT reports (TXT, PDF) and an image; byte-stable (binary in git)
│   ├── make_fixtures.py     Regenerates them deterministically
│   ├── legacy_installation.py  Fictional plaintext M3.1-A/B installations for the migration tests and demo
│   └── audit_report.py      Independent report-citation audit against the original files
├── AGENTS.md                Codex working agreement
└── .gitignore              Excludes local database, documents, scans and .env
```

## Known limitations — read before real data

**Do not import or transcribe real patient data into this version.** The repository is public; never commit real data.
See `docs/M3_THREAT_MODEL.md` for prioritised risks. Protected mode now has a local owner, a key vault and an encrypted database, but there is no document encryption yet, no authenticated backup manifests, no consent workflow, no verified data ingestion from records, no patient access-control boundary, no independent security review, and no production threat model. Attached documents, backup manifests, any plaintext pre-migration backup, and downloaded reports or exports are stored unencrypted unless the operating system's disk encryption covers them. Database encryption alone does not make this application ready for real patient data. Only run on loopback (`127.0.0.1`); do not deploy or expose it on a network.

- Records cannot be deleted (by design for now); a mistaken record can only be corrected or rejected before acceptance.
- Restore is a CLI, restores the whole database (all cases), and expects the API to be stopped (not enforced).
- The audit hash chain detects edits made outside the app, but anyone with file access can rewrite the whole chain consistently; it is tamper-evident against casual edits, not cryptographically signed.
- Attached documents are verified by hash, not by content: nothing checks that a document actually says what the transcribed values say.
- **The nontechnical-user walkthrough has not been performed** (`docs/USABILITY_M2_6.md`); that M2.6 acceptance criterion is incomplete.
- Rendered-page highlights for text-layer citations are coarse (whole pdf.js text items) and appear only for unique matches; documents rendered differently by pdf.js and pypdf may show "could not be placed".
- Extraction covers CA-125 only, from documents with a text layer. On the 8 held-out layouts recall was 28.6%: unfamiliar layouts are usually flagged for manual review rather than read. There is no OCR (`docs/OCR_INVESTIGATION.md`): images and scanned PDFs need manual, region-anchored transcription.
- The benchmark corpus and parser share an author; main-corpus scores do not demonstrate generalisation.
- Character offsets refer to the text layer of a specific extractor version (e.g. `pypdf-6.21.2-v1`). Anchors are checked when written; after a pypdf upgrade the text may differ and existing anchors are not re-validated automatically.
- Region anchors are checked for format and bounds only, never against image content. Drawing a region needs a mouse (no keyboard alternative yet).
- Findings use exact matching (same test and date, same study date and modality, …); they do not detect the same event recorded on different dates or with different wording.
- Reviewer identity is not recorded: there is no login, so "accepted" / "reviewed" / "verified" say what happened, not who did it.
- The unit registry covers CA-125 only; other tests accept any well-formed unit string.
- Lesion keys are matched exactly (case-sensitive); a mistyped key shows as unmatched rather than being merged.
- Real multi-user isolation has not been built. DICOM rendering and registration are Phase 2 work.

A single lesion dimension, CA-125 trend, or sequence of imaging reports is not sufficient to determine ovarian cancer response. All output is **descriptive** and must be checked against the original clinical documentation and the oncology team's conclusions.

See `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, and `docs/ROADMAP.md` for detailed design and the safe next steps.
