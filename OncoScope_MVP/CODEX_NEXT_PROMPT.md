# Prompt for Codex — OncoScope M2

Please read `AGENTS.md`, `README.md`, and all files in `docs/` before editing code.

**Goal:** Turn the existing Phase 1 OncoScope prototype into a demonstrably trustworthy synthetic-data medical-record organizer, without expanding into medical inference, DICOM segmentation, cloud hosting, or AI features.

Start by running the backend tests and installing/building the React frontend. Fix any build/type/runtime errors. Then exercise the full fictional-source workflow in the UI, identifying where a user cannot efficiently enter or verify source-linked information.

Prioritize: source provenance improvements, full audit-logged correction support, robust unit rules, browser testing, safe export of synthetic data, and consistent report traceability. Add database migrations if the schema changes. Provide a short implementation plan and complete each milestone with tests.

**Required demonstration:** From a new empty local database, create a fictional source, a dated CA-125 laboratory value, a fictional treatment event, two imaging report metadata records, and matched recorded lesion measurements. Verify that the chart and timeline show the same values as the database, the comparison displays only the correct difference, and the generated Markdown cites each source. Then correct a measurement or lab value, verifying that both original and corrected values appear in audit history. Exercise invalid IDs, mixed units, zero/negative measurements, and missing-source attempts.

**Stop condition:** Do not use real patient data, add a medical response classifier, make treatment recommendations, build ML/CT interpretation, deploy publicly, or invoke external AI APIs with stored records. Report only features you implemented and actually verified, and list any gaps honestly.
