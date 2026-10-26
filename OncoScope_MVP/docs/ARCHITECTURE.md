# OncoScope Phase 1 architecture

## Architectural principle

**Record observations first; display them faithfully; link back to the source.** No model may silently transform a medical finding into a clinical conclusion. Research and simulation functionality does not access or mutate clinical records.

## System diagram

```text
           Browser (React / TypeScript on 127.0.0.1:5173)
                       | /api via Vite proxy
                       v
              FastAPI on 127.0.0.1:8000
                    | validation
                    v
                   SQLite
           backend/data/oncoscope.sqlite3
             |                 |
        source-linked        audit log
        medical entries     original/corrected values

Future Phase 2: Local DICOM storage + integrated viewer (no scan bytes in SQLite)
Future Phase 3: Literature retrieval isolated from patient store
Future Phase 4: Simulation sandbox using synthetic/abstract inputs
```

## Entity model

| Entity | Main fields | Invariant |
|---|---|---|
| `patients` | label, is_synthetic | Demonstration case is flagged synthetic |
| `sources` | patient_id, label, kind, source_date, note | Human-identifiable original reference metadata |
| `events` | date, kind, title, detail, source_id | Source must belong to same patient |
| `labs` | date, test, value, unit, source_id | Units never dropped or automatically converted |
| `treatments` | date, regimen, cycle, notes, source_id | Administration records, not medication orders |
| `studies` | date, modality, study_label, report_text, source_id | Report text stored without AI interpretation |
| `measurements` | study_id, lesion_key, site, long_axis_mm, short_axis_mm, source_id | One key per study, >0 dimensions; source/patient verified |
| `audit_log` | entity_type, entity_id, patient_id, action, detail_json, reason, recorded_at, prev_hash, entry_hash | Append-only (SQLite triggers); SHA-256 hash chain; actions `create`, `correct`, `status`, `attach_document` |
| `schema_migrations` | version, name, applied_at | Mirrors `PRAGMA user_version` |
| `extractions` | source_id, document_sha256, parser, parser_version, page, start, end, original_text, proposed_json, issues_json, status, accepted_json, decision_reason, record_id | Proposals only; records are created by an audited accept |
| `document_text` | document_sha256, extractor, page, text | Derived cache; reproducible from the original file |

Since migration 2, every patient and record row also has a random `uid` (stable across exports), and every record row has
`revision` (starts at 1, +1 per correction), `verification_status` (`entered`/`reviewed`/`verified`) and `updated_at`.
Sources additionally hold `origin` and the attached document's `document_sha256`, filename, media type, size and attach time.

Dates are ISO-8601 calendar dates. Values use SQLite REAL, and units are explicit strings; comparisons only subtract values entered in **millimeters** by the API. Measurements must be captured from clinician-verified original reports, never inferred by looking at an image in the MVP.

Sources preserve metadata about originals and, optionally, one attached synthetic file (PDF/TXT/PNG/JPEG). Files are stored
content-addressed under `backend/data/documents/<sha256[:2]>/<sha256>` (configurable with `ONCOSCOPE_DOCUMENT_DIR`), cannot be
replaced once attached, and are re-hashed whenever they are served.

## Evidence model (migration 3)

Every observation (`events`, `labs`, `treatments`, `studies`, `measurements`) has `provenance`
(`transcribed` | `extracted` | `user_observation`) and an optional anchor: `anchor_page`, `anchor_quote`,
`anchor_start`/`anchor_end` (character offsets into that page of the document's text layer) or `anchor_region`
(`x,y,w,h` percent of the page/image), plus `extraction_id` for accepted proposals.

- `document_text(document_sha256, extractor, page, text)` caches the deterministic text layer. It is derived data keyed by
  the file hash and extractor version (`utf8-text-v1`, `pypdf-<version>-v1`); originals are never modified.
- Anchor rules (server-side, on create and on every correction): the source must have a document; a quote must occur on
  the stated page (or uniquely in the document) and is stored with resolved offsets; given offsets must match the quote
  exactly; text documents take quotes, images take regions, PDFs take either; user observations take no anchor.
- `extractions` holds proposals: original text, page, offsets, proposed fields, issues, parser name/version, document hash,
  status (`pending` → `accepted` | `rejected`), accepted fields, reason, and the created record. Unique per
  (source, parser, version, page, start), so re-running is idempotent. Accepting re-validates the full lab record (units,
  dates, anchor against the stored text) and refuses duplicates from the same source.
- `evidence.build_findings` derives conflict / duplicate / date / missing-support findings on read; nothing is stored or
  resolved.

## Document fidelity (M2.6)

- **Rendering:** the browser fetches the original bytes, recomputes SHA-256 with WebCrypto, and renders pages with pdf.js
  (`pdfjs-dist` legacy build, XFA disabled) only if the hash matches the record.
- **Locations:** a *text-layer citation* (page + offsets into the server's pypdf text, `anchor_quote/start/end`) and a
  *rendered-page region* (page + `x,y,w,h` %, `anchor_region`) are different things. `pdfMapping.mapQuote` places a
  text-layer citation on the rendered page only when the collapsed-whitespace quote (then, failing that, a
  whitespace-free comparison) matches exactly one run of pdf.js text items; ambiguous, absent or text-free cases are shown
  as "could not be placed" and never highlighted.
- **Benchmark:** `fixtures/benchmark/make_benchmark.py` and `make_holdout.py` generate fictional documents and manifests;
  `app.benchmark` scores the real pipeline plus an API round trip and lists every non-ideal outcome.

## Security foundations (M3.0)

- All `/api` responses carry `Cache-Control: no-store`, `Pragma: no-cache`, `X-Content-Type-Options: nosniff` and
  `Referrer-Policy: no-referrer`. Mutations accept JSON only; uploads require `application/octet-stream` and send the
  filename in `X-Document-Filename`, so no record content travels in URLs (and therefore access logs).
- `/api/audit/verify` also returns the chain head (entries, last id, last hash); backup manifests record it and restore
  compares it. Backups and restores refuse document-store entries that are not hash-named files matching their bytes.
- The proposed M3 architecture (authentication, SQLCipher, AES-GCM documents, key vault, HMAC audit chain, deletion by
  crypto-shredding, parser sandbox) is in `M3_SECURITY_ARCHITECTURE.md`; it is not implemented yet.

## Backup and restore

`app.backup` snapshots SQLite through the backup API, copies the document store and writes `manifest.json`
(schema version, `PRAGMA integrity_check`, audit-chain verification, per-table row count and SHA-256 of all rows,
per-document hash check). `app.restore` verifies the backup against its manifest, refuses conflicts in the target
document store, backs up any existing target first, restores, and compares the restored copy with the manifest.

## Migrations

`db.MIGRATIONS` is an ordered list of `(version, name, function)`. `init_db()` (run at API start-up and by the seed script) applies
each pending migration in its own transaction, records it in `schema_migrations`, and sets `PRAGMA user_version`. Migration 1 is the
Phase 1 schema with `IF NOT EXISTS`, so unversioned Phase 1 databases adopt it unchanged; migration 2 adds the M2 columns, backfills
uids, audit `patient_id`/`reason` and the hash chain, then installs the append-only triggers. A database newer than the code is
refused. Released migrations are never edited; schema changes append a new migration and a test that upgrades an older database.

## Corrections

`PATCH /api/records/{type}/{id}` takes only the changed fields plus a reason. The server merges them into the stored row and
re-validates the whole record with the same model used for creation (units, dates, measurement rules, source/study ownership),
so a correction can never produce a record that could not have been entered. `patient_id`, ids, uids and document fields are
not correctable. The audit entry stores the complete before and after rows, the changed field names and the reason.

## Frontend screens

- **Overview:** record counts, latest timeline entries, CA-125 chart, source-integrity reminders.
- **Timeline:** events, measurements indirectly through studies, treatment entries, laboratory results in one chronology.
- **Laboratory:** CA-125 values in U/mL, tables with all units, treatment entries; logged lab correction UI.
- **Imaging:** text radiology reports and manually sourced lesion measurement differences by stable lesion key; no DICOM viewer yet.
- **Sources:** source IDs, kind, document date, origin, attached document hash and verification, citing records.
- **Audit:** all audit entries with field-level before → after values and hash-chain status.
- **Documents:** text layer or image of each attached file, cited passages/regions highlighted, selection → anchored transcription, CA-125 proposal button.
- **Review:** extraction proposals beside their original text; accept / correct / reject with reasons.
- **Evidence:** findings, records per date and per document, consecutive value differences.
- **History:** date picker → everything recorded that day with documents, conflicts and pending proposals; CA-125 chart with treatment dates and unreviewed markers.
- **Documents (M2.6):** rendered PDF pages (zoom, navigation) or text layer; pointer or keyboard region selection; one-step import.
- **Add record:** typed forms that require the user to register and select a source first.

## Validation and interpretation boundaries

1. FastAPI/Pydantic validates allowed event and source types, dates, finite numbers, required units, positive dimensions and identifiers.
2. Foreign keys and server-side checks prevent records from referencing another patient's source or imaging study.
3. Comparison pairs explicit keys in two studies; a missing key is shown as missing, never assigned a zero length. Long-axis difference is `followup_mm - baseline_mm`, computed in decimal; not a RECIST computation. The earlier study may not be dated after the later one; a site change under one key is flagged.
4. The CA-125 visualization plots one exact unit (`U/mL` by default) from `/labs/series`, with a calendar-date x axis, no conversion and no reference-range judgment. Results in other units are listed beneath the chart. Test names match ignoring case, spaces and hyphens (`CA 125` = `CA-125`).
5. Report exports are descriptive and include source IDs. The original document itself is NOT embedded.
6. Corrections to any record require a reason, keep before/after values in audit history, increment the revision, and may re-point the record only to another source of the same patient.
7. Nothing in the app labels disease stage, assigns a response score, or identifies a treatment as successful or unsuccessful.

## Follow-up architecture choices

For future data ingestion, add a document-store abstraction that records original binary, a SHA-256 hash, source type, precise creation/collection time, provenance, extractor version, and human-verification status. Separate patient-authored notes from signed clinical findings. Extracted values should remain pending review until explicitly confirmed. A normalized medical ontology may be considered only once the raw-record pipeline and unit policy are working.

Image-viewing functionality should reuse vetted components (e.g. OHIF or vtk.js-based tooling) and DICOM infrastructure. Study registration and scan-to-scan comparison require acquisition metadata and expert oversight. Do not build a custom inference model as part of that infrastructure.
