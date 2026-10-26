# M2.6 benchmark corpus (fictional)

26 fictional documents in `documents/` (22 lab/letter/scan stand-ins, 4 radiology reports) and a hand-written ground
truth in `manifest.json`. Regenerate byte-identically with `python fixtures/benchmark/make_benchmark.py`.
Nothing here describes a real person, clinic, result or date of care.

Patterns covered: plain key-value, markdown pipe table, fixed-width table with flag and reference columns, PDF tables with
separately drawn cells, cumulative report with row dates and repeated CA-125 values, multi-page PDF with the date and the
result on different pages, two-column PDF, label variants (`Cancer antigen 125`, `CA125`), unit before value, ISO /
named-month / dotted / ambiguous numeric dates, collection vs report dates, report date only, missing unit, unaccepted
unit, `<` and `>` qualified values, decimal comma, value on the next line, amended result with a prior value, narrative
mentions without results, a PDF without a text layer, an image, conflicting and duplicate reports across documents, and
radiology reports with and without measurements.

## manifest.json

Per document: `id`, `file`, `sha256`, `media_type`, `category`, `layout`, `patterns`, `text_layer`, `source_date`
(the document's own date, used as the source date when importing), `notes`, and:

- `expected_observations` — CA-125 statements in the document:
  - `status: complete` — every field is stated unambiguously; a correct extractor returns `value`, `unit`, `date` exactly
    and raises **no** issue.
  - `status: flagged` — the document is ambiguous, incomplete or unsupported at this point; a correct extractor must raise
    at least one code from `expected_issues`. Fields the document does state (`value`, `unit`, `date` when not null) must
    still be read correctly. `qualifier` records bounds such as `<5`, which are never turned into numbers.
  - `status: mention` — CA-125 is named without a result; no observation exists, and any proposal must carry an issue.
  - `status: no_text` — the value is printed in a file without a text layer; extraction must report that manual
    transcription is required.
  - `page` and `evidence` — where the statement is: `evidence` must occur in the extracted text of that page and in the
    proposal's cited passage.
- `expected_manual` — radiology measurements for manual, source-anchored transcription (`lesion_key`, axes, `page`, `evidence`).

`expected_findings` lists cross-document conflicts and duplicates that must be reported (not resolved) once the
complete observations are accepted.
