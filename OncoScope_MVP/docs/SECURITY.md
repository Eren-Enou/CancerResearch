# Privacy, consent, and safety gates

## Current classification: **Synthetic-only prototype**

No real names, dates of birth, patient IDs, pathology records, medical images, scanned documents, or actual observations should be entered in the current application. All included fixture values are fictional. This is not healthcare software ready for storing protected health information.

The app does **not** provide user login, per-user authorization, encryption-at-rest, encrypted backup, cloud synchronization, file ingestion safety checks, or a patient permission workflow. It should not be deployed to Vercel, hosted on a home LAN, or shared publicly. Even on localhost, other software running under the same operating-system account might access its local database.

## M3.1-A protected local access
Protected mode (default) requires an owner-passphrase session for every case-data API request; new audit entries name
the owner; foreign origins and non-loopback peers are refused; the UI locks and discards case data on lock, inactivity,
session end and 401. Design, verification and **remaining risks**: `M3_1A_PROTECTED_ACCESS.md`. **No encryption at rest
yet** — the lock does not protect the files on disk.

## M3.0 documents
`M3_DATA_INVENTORY.md` (where data lives), `M3_THREAT_MODEL.md` (prioritised risks, confirmed defects, limitations),
`M3_SECURITY_ARCHITECTURE.md` (proposed controls), `M3_1_IMPLEMENTATION_PLAN.md` (first increment), and the regression
suite `backend/tests/test_security.py`. The repository is **public**: fixtures, screenshots and CI logs must stay fictional.

## Existing controls

- Protected mode: Argon2id passphrase verifier (`argon2-cffi`), 256-bit in-memory session tokens in the `Authorization`
  header only, idle/lifetime expiry, immediate revocation on lock, rate-limited unlock, actor on every new audit entry.
- Requests from non-loopback peers, foreign `Origin`s or `Sec-Fetch-Site: cross-site/same-site` are refused (403).
- FastAPI and Vite examples bind to `127.0.0.1`, not `0.0.0.0`.
- No third-party AI API, analytics tracker, telemetry integration, or external database.
- Local synthetic document attachment only: PDF/TXT/PNG/JPEG, ≤ 10 MB, signature-checked, no HTML/SVG/DICOM; served with `nosniff` and `Content-Security-Policy: sandbox`; uploads require `application/octet-stream`, which other web origins cannot send without a CORS preflight the API never grants.
- The API rejects requests whose Host header is not `127.0.0.1`/`localhost` (DNS-rebinding protection).
- Audit log is append-only at the SQLite level and hash-chained; `GET /api/audit/verify` detects out-of-band edits.
- Validation errors report field and message only; submitted values are not echoed back.
- Export (JSON/CSV) is refused for any case not flagged synthetic; CSV text cells are guarded against formula injection.
- Extraction is local, deterministic and rule-based (no model, no network). PDF parsing uses pypdf in-process; a malformed
  PDF is reported as unreadable. Parsing untrusted PDFs is attack surface that must be isolated before real documents.
- The browser renders PDFs with pdf.js after re-checking the file's SHA-256; XFA is disabled and annotations/scripts are
  not rendered. pdf.js parsing untrusted PDFs in the browser is additional attack surface to review before real documents.
  The legacy build is used for compatibility (polyfills), and should be kept updated.
- No caching: every `/api` response is `no-store`; no record content in URLs or access logs (regression test runs the real
  server and searches its log for sentinel values).
- CI hygiene job blocks committed databases, document stores, backups, documents outside fictional fixtures and secrets.
- Backups include a verification manifest; restores verify before and after and never overwrite an existing database or
  differing document bytes without keeping them. Backups and the derived `document_text` cache are **unencrypted**.
- Input constraints and patient/source ownership checks.
- Git excludes SQLite databases, DICOM files, `.env` files, local data directories (including attached documents and backups), and Playwright output.
- Manual/synthetic records preserve source linkage; initial audit history retained for writes.
- No embedded diagnosis, RECIST result classification, or treatment suggestions.

## Minimum safeguards before *any* real-data pilot

1. Explicit, informed permission from the person whose records will be stored; clarify device access, retention, deletion, and backups.
2. Verify legal, organizational and regulatory obligations applicable to the intended use and jurisdiction. Do not assume "local" automatically means compliant with HIPAA or other privacy law.
3. Threat-model access to local files, browser process, backups, and API endpoints. Provide authentication/access controls where appropriate.
4. Enable and verify full-disk encryption, a secure credential/key strategy, encrypted backups, recovery tests, and strict file access permissions.
5. Add per-case deletion/export capability and a documented retention policy.
6. Add document integrity hashes, immutable source retention, extraction provenance, human verification and an append-only/tamper-evident audit design.
7. Review file import and DICOM metadata for direct identifiers and accidental log exposure; avoid copying patient content to AI services without permission.
8. Run security tests and independent review before production/personal clinical use.
9. Maintain an explicit non-clinical disclaimer and documented clinician-verification workflow.

## Scientific safety

Changes in CA-125, ascites, symptoms, or any single target measurement should not be treated as decisive evidence of treatment response. Ovarian/peritoneal disease can be challenging to quantify using simple tumor measures. All patient-specific clinical conclusions belong to the oncology professionals caring for the patient. Experimental simulations must never silently write to the clinical-data store.
