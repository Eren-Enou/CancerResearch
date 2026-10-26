# OncoScope development milestones

## M0 — Clinical information contract (delivered)

**Goal:** Model source-linked observations and mark invented data unmistakably.

**Acceptance:** Every record has a date/units as applicable and original-source ID; cross-patient source references fail; no clinical assessments are computed.

## M1 — Phase 1 vertical slice (delivered prototype)

**Goal:** Connect local API, SQLite, synthetic seed, React UI, timelines, lab trend, study comparisons, and Markdown report.

**Acceptance:** Eight backend tests passing; lab correction retains before/after; repeated seed does not duplicate; example report cites source identifiers. React UI has source-first entry forms and local API proxy. **Frontend build remains unverified until dependencies are available.**

## M2 — Harden Phase 1 into a truly usable record-organizer

**Status: largely delivered (see `docs/M2_PLAN.md` and the README).** Delivered and tested: strict TypeScript build, blank-database
workflow in the UI, synthetic document attachment with SHA-256, audited correction of all record types with verification states,
unit registry and mixed-unit handling, record UIDs and UTC system timestamps, JSON/CSV export, backup CLI, Vitest component tests,
a Playwright end-to-end run of the required demonstration, wider API edge-case tests, and versioned migrations.
Still open: restore command, accessibility audit with assistive technology, inline PDF viewing check, the separate
privacy/authentication/encryption plan, and the nontechnical-user exit test below (not yet performed by a person).

- Install node dependencies in the developer's environment and run strict TypeScript build; fix any compilation and browser findings.
- Run a complete workflow from blank database using only *new synthetic* sources and observations, including audited corrections and report export.
- Add source-document file storage with hashes and linked original-file viewer, while continuing to use entirely synthetic files.
- Implement audit-logged editing for all record types, with immutable original values and explicit verification states (`entered`, `reviewed`, `verified`).
- Make unit handling explicit and test against mixed lab units; add unique observation IDs and robust timestamp/time-zone handling.
- Add read-only export of a structured dataset (JSON/CSV) and non-destructive backup/restore for demo data.
- Add frontend component/end-to-end tests and accessibility review, plus wider API edge-case/security tests.
- Define a reproducible migration strategy for SQLite schema changes.
- Prepare a separate privacy/authentication/encryption plan. **Continue synthetic-only development until reviewed.**

**Exit test:** A nontechnical user enters a fictional case solely from synthetic documents, observes accurate chronology, corrects a transcription mistake, and produces a report where every fact can be traced to its exact fictional source. Another developer verifies every reported number and source from raw fixtures.

## M3 — Real-data readiness review (not yet approved)

**Goal:** Meet privacy/consent, access, encrypted-storage/backup, original-document verification, audit-integrity, and legal requirements defined in `SECURITY.md`.

**Exit test:** Documented threat model and controlled import demonstration; independent privacy/security review. Real patient data remains out of scope until this milestone passes.

## M4 — DICOM viewing (Phase 2)

**Goal:** Import and view local medical imaging using established tooling, link studies and expert-defined annotations, show acquisition/registration limitations.

**Exit test:** Synthetic/public non-identifiable DICOM studies load reproducibly; scanned overlays never imply unvalidated diagnosis.

## M5 — Literature and molecular context (Phase 3)

**Goal:** Verified pathology and molecular variables can be linked to references, with explicit evidence status and no treatment-selection engine.

## M6 — Computational biology sandbox (Phases 4–5)

**Goal:** Separate, reproducible abstract tumor and immune-system simulations benchmarked against known mathematical behavior and public reference datasets. No linkage from hypothetical simulation output into verified clinical findings.
