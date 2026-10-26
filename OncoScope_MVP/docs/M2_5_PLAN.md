# M2.5 — Verified evidence and case reconstruction (synthetic-only)

Scope guard: fictional documents only; local-first; no external AI; no diagnosis, staging, response classification or
recommendations. Extraction proposes transcriptions; a person decides. Verifying a transcription does not verify a diagnosis.

## Baseline (start of M2.5)

Working and preserved: 28 backend tests, strict TypeScript build, 12 Vitest tests, Playwright M2 demonstration; audited
corrections for all record types; hash-chained audit log; document attachment with SHA-256; unit registry; JSON/CSV export;
backup CLI; versioned migrations (schema v2).

Missing: a way to *read* attached documents in the app (PDF inline viewing unverified), any link from an observation to a
location inside its document, a transcribed-vs-user-observation distinction, extraction of any kind, a review queue, views
for conflicts/duplicates/missing evidence, a restore workflow, and backup manifests to verify a restore against.

## Milestones and acceptance criteria

### A. Evidence anchors and provenance (migration 3)
- Every observation table gains `provenance` (`transcribed` | `extracted` | `user_observation`), `anchor_page`,
  `anchor_quote`, `anchor_start`, `anchor_end`, `anchor_region`, `extraction_id`. Existing rows become `transcribed`, unanchored.
- **Accept:** a v2 database upgrades to v3 in place with data, audit chain and hashes intact.
- **Accept:** an anchor quote must occur in the source document's extracted text (on the stated page); offsets are resolved
  and stored; an ambiguous quote (several occurrences, no offsets) or an absent quote is refused with 422.
- **Accept:** user observations cannot carry a document anchor; image documents take a percentage region `x,y,w,h`, not a quote.
- **Accept:** anchors and provenance are correctable through the existing audited correction path.

### B. Document text and viewing
- Deterministic text extraction: TXT (UTF-8) and text-layer PDF (pypdf, per page), cached by SHA-256 + extractor version.
- **Accept:** `GET /api/sources/{id}/document/text` returns pages; originals stay byte-identical (hash re-verified).
- **Accept:** attaching the same bytes to a second source of the same case is refused as a duplicate import.
- UI: document viewer beside recorded information — text pages with anchored passages highlighted, images with region
  overlays, original-file link; every observation's source reference opens the viewer at its anchor.

### C. Extraction pipeline and review queue
- Parser `ca125-lab` v1: line-based rules for CA-125 test name, numeric value, unit and an ISO / unambiguous day-month-name
  date (line date, else a single labelled collection date).
- Each proposal stores original text, page, offsets, proposed fields, issues, parser name/version, document hash, status.
- Issues are reported, never guessed: `value_not_found`, `value_qualifier` (`<5`), `unit_missing`, `unit_not_accepted`,
  `date_missing`, `date_ambiguous` (e.g. `09/10/2026`, or several collection dates), `date_after_document`.
- **Accept:** re-running extraction on the same document creates no duplicate proposals.
- **Accept:** accepting creates a lab with provenance `extracted`, the proposal's anchor, status `reviewed` (never
  `verified`); any field changed at acceptance requires a reason and keeps proposed vs accepted values; missing fields must be
  supplied; a duplicate of an existing lab is refused. Rejecting requires a reason. Decisions are audited.
- Radiology: manual, source-anchored transcription from a text selection in the viewer (no automated extraction).

### D. Evidence views and report
- `GET /api/patients/{id}/evidence` returns findings without resolving them: conflicting values for the same test/date from
  different sources, possible duplicate events/studies/treatments, observations dated after their source document,
  unanchored transcriptions, sources without documents, pending extractions, measurements whose source differs from their study's.
- UI: findings, "recorded on each date", "observations by document", consecutive same-unit lab differences.
- Report: every observation line names provenance, document filename, short hash, page and quoted passage; findings section.
- **Accept:** a test parses the report and checks each quoted passage against the original fictional file.

### E. Backup and restore
- Backup writes `manifest.json`: schema version, per-table row counts and content hashes, audit-chain head, document hashes.
- `python -m app.restore <backup> --target <db>`: verifies the backup first (SQLite integrity, audit chain, every document
  re-hashed, manifest match), never overwrites an existing target without first preserving it, then re-verifies the restored copy.
- **Accept:** restored case has identical table hashes, valid audit chain with original values and reasons, identical
  document bytes; a tampered backup is refused.

### F. Tests and demonstration
- Backend tests for duplicate imports, invalid values, mixed units, mismatched dates, unsupported documents,
  source-ownership violations, malformed extraction results, restore.
- Playwright: the 11-step M2.5 demonstration on a fresh database, including backup/restore through the CLI.

## Outcome

All milestone acceptance criteria above are covered by automated tests: `backend/tests/test_m25_evidence.py` (A, B),
`test_m25_extraction.py` (C), `test_m25_findings.py` (D, report audit against original files), `test_m25_restore.py` (E),
and `frontend/e2e/reconstruction.spec.ts` (F, 11-step demonstration on a fresh database). Known limitations are listed
in the README; remaining M3 work is in `docs/ROADMAP.md`.
