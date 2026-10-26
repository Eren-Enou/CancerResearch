# M2.6 — Document fidelity, extraction reliability and usability (synthetic-only)

Scope guard: fictional documents only, local-first, no external AI, no OCR dependency in the core path, no diagnosis,
response classification or recommendations. Nothing merges to `main` without the owner's authorization.

## Phase 0 baseline

- PR #1 (M2.5) and PR #2 (`CLAUDE.md` workflow) are open and unmerged; `main` holds only the initial commit.
- M2.6 is therefore branched from `feat/m2.5-evidence-reconstruction` (not `main`) as `feat/m2.6-document-fidelity`.
  Its PR targets `main` per the workflow and lists #1 as a prerequisite; its diff includes M2.5 until #1 merges.
- Baseline on that branch: 60 backend tests, strict TypeScript build, 18 Vitest tests, 2 Playwright demonstrations — all pass.

## Architecture decisions

1. **PDF rendering with pdf.js** (`pdfjs-dist` 6.3, Mozilla, Apache-2.0). Pages render to canvas with zoom, page
   navigation and scrolling. The browser fetches the original bytes, recomputes SHA-256 with WebCrypto and refuses to render
   on mismatch, in addition to the server-side hash check. `isEvalSupported: false`; scripting/annotations are not rendered.
2. **Two kinds of location, never conflated.**
   - *Text-layer citation*: page + character offsets into the server's deterministic text layer (pypdf). This is what the
     report cites and `audit_report.py` verifies.
   - *Rendered-page region*: page + `x,y,w,h` percent of the rendered page, drawn by the user (pointer or keyboard).
   A text-layer citation is shown on the rendered page only if the quoted passage matches **exactly one** run of pdf.js
   text items on that page; the highlight is labelled "mapped from text layer". Zero or several matches show an explicit
   "could not be mapped reliably" notice and no highlight.
3. **Keyboard region selection**: a focusable selection box moved with arrow keys and resized with Shift+arrows, plus
   numeric `x,y,w,h` inputs; the pointer drag is an equivalent shortcut, not the only way.
4. **Benchmark corpus as code**: `fixtures/benchmark/make_benchmark.py` writes ≥ 20 fictional documents and
   `manifest.json` (ground truth: expected observations, values, units, dates, qualifiers, pages, evidence text, expected
   issues, expected cross-document findings) deterministically. Ground truth is written by hand in the generator, never
   derived from parser output.
5. **Parser v2** (`ca125-lab` v2): layout-independent rules (label variants, table separators, row dates, collection vs
   report dates, time suffixes, flags, reference ranges, value on next line, multiple values on one line). Ambiguity,
   unsupported formats and missing data stay explicit issues. Re-running a new parser version never duplicates a pending
   proposal for the same passage.
6. **Evaluation tool** `python -m app.benchmark`: runs the real pipeline on the corpus and scores it against the manifest;
   writes JSON and Markdown. The headline gate is **zero silently incorrect results** (issue-free proposals whose fields
   differ from ground truth, or issue-free proposals where ground truth expects none).
7. **OCR**: investigated only (documented); not a dependency. Text-free documents go to manual region transcription.

## Milestones, tests and success criteria

| # | Milestone | Tests | Success criteria |
|---|---|---|---|
| A | Benchmark corpus + manifest | manifest/file hash consistency; every manifest evidence string occurs on its page | ≥ 20 fictional documents covering all listed patterns; reproducible bytes |
| B | Parser v2 | parser unit tests per layout; API idempotency across versions | supported patterns extracted; ambiguity/missing/unsupported flagged |
| C | Evaluation tool | test runs the benchmark and asserts the gates | 0 silent incorrect; recall on supported patterns reported; anchor accuracy 100% for matched proposals; failures listed by type |
| D | pdf.js viewer | Vitest for mapping/keyboard logic; Playwright renders pages, zooms, navigates, maps/declines highlights, selects a region by keyboard | rendered pages visible; integrity checked in browser; no wrong highlights |
| E | Case history view + one-step import | Vitest + Playwright | date → observations + documents; chart with treatments, conflicts and unreviewed markers; every value links to evidence |
| F | Usability | protocol + heuristic review findings; fixes for the top issues | human walkthrough recorded **or explicitly marked incomplete** |
| G | Regression | all suites | M2 and M2.5 demonstrations still pass |

## Outcome

| # | Result |
|---|---|
| A | 26 fictional documents + manifest; corpus tests pass |
| B | Parser v2.1; v1 baseline recorded (`docs/benchmark/BASELINE_ca125-lab_v1.md`) |
| C | Gates pass on the main corpus and the held-out set (0 silent errors); held-out recall 28.6%; see `docs/benchmark/README.md` |
| D | pdf.js viewer with zoom, navigation, in-browser hash check, unique-match highlights, keyboard regions — Playwright verified in Edge 138 |
| E | Case history + one-step import — Vitest and Playwright verified |
| F | Protocol written; heuristic issues U1–U6 fixed; **human walkthrough not performed — criterion incomplete** |
| G | 80 backend, 27 frontend, 3 Playwright demonstrations pass |
