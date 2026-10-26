# M2 implementation plan (synthetic-only)

Scope guard: no real data, no response classification, no recommendations, no imaging inference,
no network exposure, no external AI. Every milestone ships with tests.

## Findings from the Phase 1 walkthrough (blank database)

| # | Finding | Effect on a user |
|---|---|---|
| F1 | No way to create a case from an empty database (UI shows "run the seed script") | The required blank-database workflow cannot start in the UI |
| F2 | Only lab values can be corrected; the correction UI cannot change the source | Transcription errors in events, treatments, studies, measurements, sources are permanent |
| F3 | No per-record history in the UI; audit only via raw API JSON | Original vs corrected values cannot be verified in the app |
| F4 | Lab units are free text (`u/ml`, `U/mL`, `U/ml` split one series); the chart silently drops other units | Mixed units are easy to create and hard to notice |
| F5 | Timeline omits lesion measurements; report uses `:g` formatting (`1234567` → `1.23457e+06`) and different number formats for long/short axes | Displayed values may not match stored values |
| F6 | Report cites source labels but has no source register, record IDs, or correction markers | Facts cannot be traced to an exact record/source revision |
| F7 | Sources hold metadata only; no file, no hash | Provenance cannot be checked against an original (synthetic) document |
| F8 | No schema versioning; `CREATE TABLE IF NOT EXISTS` only | Schema changes would break existing local databases |
| F9 | No structured export; audit log has no patient column and is mutable | Export/verification relies on ad-hoc joins |
| F10 | Comparison accepts a "later" study dated before the "earlier" one and ignores site changes under one key | Sign of the difference can be mislabeled |

## Milestones

1. **Schema migrations** — `PRAGMA user_version` + `schema_migrations` table; migration 2 adds record
   UIDs, revision counters, verification status, source origin/document columns, audit `patient_id`,
   hash chain and append-only triggers. Test: upgrade a Phase 1 database in place without data loss.
2. **Audited corrections for every record type** — `PATCH /api/records/{entity}/{id}` (full
   re-validation of the merged record, mandatory reason, revision++), per-record history, verification
   status transitions, audit hash-chain verification. UI editor + history on every record.
3. **Unit and value rules** — known-test registry (CA-125: `U/mL` or `kU/L`, never negative, never
   converted), case-variant spellings rejected, mixed-unit series endpoint used by the chart, measurement
   rules (0 < mm ≤ 1000, short ≤ long, strict lesion keys), no future observation dates, single-line fields.
4. **Source provenance** — synthetic document attachment (PDF/TXT/PNG/JPEG, magic-byte checked,
   10 MB cap, content-addressed, SHA-256, immutable once attached), integrity verification on read.
5. **Report traceability + safe export** — record IDs, source IDs, revision markers, source register,
   audit chain status; JSON and CSV export refused for non-synthetic cases, CSV formula-injection guard;
   local backup CLI.
6. **Frontend + browser tests** — blank-database case creation, editors, audit view, chart from the
   series endpoint, measurements in timeline; Vitest component tests and a Playwright end-to-end run of
   the required demonstration against a fresh database.
