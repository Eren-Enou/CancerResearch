# M2.6 usability — nontechnical walkthrough protocol and findings

## Status

**Human walkthrough: INCOMPLETE (still outstanding after M3.0).** No independent, nontechnical participant was available during M2.6. The acceptance
criterion "a test participant can complete the tasks without code or database access" is therefore **not met yet**.
Automated browser tests (`npm run test:e2e`) and the developer's heuristic review below do not substitute for it.

## Protocol (for a facilitator)

- **Participant:** someone who has not used OncoScope and does not program. Explain only: "This organises *made-up*
  medical documents. Nothing here is real or gives medical advice."
- **Setup:** start the app on an empty database (README quick start without the seed). Provide the files from
  `fixtures/benchmark/documents/`: `B03`, `B04`, `B16`, `B07`, `B20`, `R02`. Do not help unless the participant is stuck
  for 3 minutes; note each hint.
- **Tasks** (record time, success, hints, errors, quotes):
  1. Create a fictional case.
  2. Import the six documents.
  3. Ask the app to propose CA-125 values from the lab documents.
  4. Review the proposals: accept the ones that match the document; explain what you do with the one whose date is unclear.
  5. The scanned PDF (`B20`) shows a result as a dark band. Record CA-125 260 U/mL on 2026-08-21 from it, marking where it is.
  6. Find everything recorded on 18 September 2026. Which documents say what? Does the app say which one is right?
  7. One value was typed wrong (facilitator changes one deliberately beforehand). Find it in its document and correct it.
  8. In your own words: what happened to the CA-125 values over time, and which values have not been checked yet?
- **Success:** tasks 1–7 completed without code/database access, ≤ 1 hint each; task 8 answered without claiming a cause
  or treatment effect the documents do not state.
- **Debrief:** SUS questionnaire; "What was confusing?"; "Did anything look more certain than it should?"

## Findings record (fill in during the session)

| # | Task | Problem observed | Severity (1–4) | Hint given | Participant quote |
|---|---|---|---|---|---|
|   |      |                  |                |            |                   |

## Developer heuristic review (cognitive walkthrough of the same tasks) — not a user test

| # | Problem found | Impact | Action in M2.6 |
|---|---|---|---|
| U1 | Importing needed two screens (create source, then attach file in Sources) | High | **Fixed:** one-step "Import a fictional document" in Documents; duplicates refused before anything is created |
| U2 | Reloading the page lost the selected case and view | High | **Fixed:** case and view kept in the URL hash |
| U3 | PDFs were shown as extracted text, not as the document the user recognises | High | **Fixed:** rendered pages (pdf.js) with zoom and page navigation; text layer one tab away |
| U4 | Evidence regions could only be drawn with a mouse | High (accessibility) | **Fixed:** keyboard region selection and numeric x,y,w,h |
| U5 | No single place answering "what was recorded on this date?" | High | **Fixed:** Case history date picker with records, documents, conflicts and pending proposals |
| U6 | Citation-status list pushed the rendered page below the fold | Medium | **Fixed:** one-line summary, expanded automatically when something could not be placed |
| U7 | Terms such as "provenance", "anchor", "text layer" are developer language | Medium | **Improved in M3.0:** plain labels ("Where this value comes from", "copied from a document", "found automatically, then checked", "not yet checked / checked / verified", "Extracted text", "change history") and a "Words used in OncoScope" glossary on the Overview. Still unvalidated with a real participant |
| U8 | Long review queues (13+ proposals) require much scrolling; no bulk "accept all issue-free" | Medium | Open — deliberately not added (bulk acceptance weakens per-item review); revisit with user data |
| U9 | No first-run guidance | Medium | Open |
| U10 | Colour contrast of badges/overlays on the dark theme not measured | Medium (accessibility) | **Measured and fixed in M3.0:** 7 text colours were below WCAG AA (lowest 3.25:1); all now ≥ 4.5:1 on every background they can appear on, enforced by `src/contrast.test.ts`. Screen-reader pass still open |
| U11 | The date strip grows long for many dates | Low | Open |
