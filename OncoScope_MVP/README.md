# OncoScope — Phase 1 local prototype (M2 hardening)

**Status:** Source-linked medical-record organizer built with synthetic data only. Not a medical device or an oncology decision-support tool.

**Core design goal:** Given two sets of entered records, reproduce their chronology and documented differences, always pointing back to the recorded sources. Never infer stage, efficacy, prognosis, or treatment recommendations.

This repository is intentionally narrow in scope: a **FastAPI + SQLite API and a React + TypeScript dashboard**. It does not contain medical scans, a real patient's information, machine learning, RECIST response logic, or molecular simulation.

## What's implemented

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
- Non-destructive local backup: `python -m app.backup`
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
python -m app.seed        # optional: fictional demo case; skip to start from an empty database
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
python -m app.seed
uvicorn app.main:app --host 127.0.0.1 --port 8000
# separate terminal:
cd OncoScope_MVP/frontend
npm install && npm run dev
```

### Run tests

```powershell
cd OncoScope_MVP\backend
python -m pytest -q                 # 28 API, migration, audit, unit-rule and export tests

cd ..\frontend
npm run build                       # strict TypeScript + production build
npm test                            # Vitest component/unit tests (12)
npm run test:e2e                    # Playwright: required M2 demonstration in a real browser
```

`npm run test:e2e` starts its own API on port 8010 with a **new empty database** in the OS temp directory and a Vite server on port 5180, then drives the UI. It uses the installed Microsoft Edge (`PW_CHANNEL=chrome` to use Chrome instead; on non-Windows set `ONCOSCOPE_PYTHON` if the venv is elsewhere). No browser download is needed.

## User workflow

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
| GET | `/api/health` | Local API health and schema version |
| GET / POST | `/api/patients` | List / create fictional cases (always synthetic) |
| GET | `/api/patients/{id}/dashboard` | Patient metadata and counts |
| GET / POST | `/api/patients/{id}/sources` / `/api/sources` | Register source documents |
| PUT / GET | `/api/sources/{id}/document` | Attach (once) / open a synthetic source file |
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
| GET | `/api/patients/{id}/report.md` | Descriptive Markdown report |
| GET | `/api/patients/{id}/export.json` · `/export/{table}.csv` | Read-only synthetic export |

`{type}` is one of `sources`, `events`, `labs`, `treatments`, `studies`, `measurements`. Clinical dates are ISO `YYYY-MM-DD` calendar dates (observations cannot be dated in the future; `plan` events can). System timestamps (`entered_at`, `updated_at`, audit `recorded_at`) are UTC with a `Z` suffix; rows created before migration 2 keep SQLite's `YYYY-MM-DD HH:MM:SS` UTC form. The app performs **no unit conversion**.

## Repository map

```text
OncoScope_MVP/
├── backend/
│   ├── app/
│   │   ├── main.py          FastAPI routes, corrections, comparisons
│   │   ├── db.py            SQLite migrations, transactions, hash-chained audit writes
│   │   ├── models.py        Pydantic request validation (dates, single-line fields, measurement rules)
│   │   ├── units.py         Lab unit registry, number formatting, exact differences
│   │   ├── documents.py     Content-addressed synthetic document store (SHA-256)
│   │   ├── reporting.py     Markdown report, JSON/CSV export
│   │   ├── backup.py        Non-destructive backup CLI
│   │   └── seed.py          Explicitly synthetic ovarian-monitoring example
│   ├── tests/test_api.py    Phase 1 API regression tests
│   ├── tests/test_m2.py     M2 tests incl. the required demonstration from a blank database
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── src/App.tsx          Views and data loading
│   ├── src/components/      Chart, correction/history tools, forms, sources, audit
│   ├── src/records.ts       Editable-field specs, change detection, number display
│   ├── src/*.test.ts(x)     Vitest tests
│   ├── e2e/                 Playwright end-to-end demonstration
│   └── src/styles.css       Responsive visual design
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SECURITY.md
│   ├── ROADMAP.md
│   └── M2_PLAN.md           M2 findings and milestone plan
├── AGENTS.md                Codex working agreement
└── .gitignore              Excludes local database, documents, scans and .env
```

## Known limitations — read before real data

**Do not import or transcribe real patient data into this version.** This MVP has no authentication, no application-layer database or document encryption, no automatic encrypted backup, no consent workflow, no verified data ingestion from records, no patient access-control boundary, and no production threat model. SQLite content, attached documents and report text are stored unencrypted unless the operating system's disk encryption covers them. Only run on loopback (`127.0.0.1`); do not deploy or expose it on a network.

- Records cannot be deleted (by design for now); a mistaken record can only be corrected. There is no restore command; backups are restored by copying the backup file over a stopped database.
- The audit hash chain detects edits made outside the app, but anyone with file access can rewrite the whole chain consistently; it is tamper-evident against casual edits, not cryptographically signed.
- Attached documents are verified by hash, not by content: nothing checks that a document actually says what the transcribed values say.
- Inline viewing of attached PDFs has not been verified in a browser (served with `Content-Security-Policy: sandbox`, which some browsers refuse to render inline for PDFs). Text and image documents and hash verification were verified.
- The unit registry covers CA-125 only; other tests accept any well-formed unit string.
- Lesion keys are matched exactly (case-sensitive); a mistyped key shows as unmatched rather than being merged.
- Real multi-user isolation has not been built. DICOM rendering and registration are Phase 2 work.

A single lesion dimension, CA-125 trend, or sequence of imaging reports is not sufficient to determine ovarian cancer response. All output is **descriptive** and must be checked against the original clinical documentation and the oncology team's conclusions.

See `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, and `docs/ROADMAP.md` for detailed design and the safe next steps.
