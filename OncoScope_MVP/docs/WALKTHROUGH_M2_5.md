# M2.5 demonstration walkthrough — reconstructing a fictional case from documents

Everything below uses the fictional files in `fixtures/fictional/` (regenerate byte-identically with
`python fixtures/make_fixtures.py`). No real person, clinic or result is described. The same steps run automatically in
`frontend/e2e/reconstruction.spec.ts` (`npm run test:e2e`), on a fresh empty database each time.

## 0. Start from an empty database

```powershell
cd OncoScope_MVP\backend
$env:ONCOSCOPE_DB_PATH = "$env:TEMP\oncoscope-walkthrough\oncoscope.sqlite3"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# second terminal
cd OncoScope_MVP\frontend; npm run dev     # open http://127.0.0.1:5173
```

## 1. Create the case
The empty database shows **No case yet** → enter `Fictional Case M25` → **Create fictional case**. It is always flagged synthetic.

## 2. Import the documents
For each file: **Add record → Source** (label, type, document date), then **Sources → Attach synthetic document**.

| Source label | Type | Document date | File |
|---|---|---|---|
| Imaginary Valley lab 2026-08-07 | lab | 2026-08-08 | `lab_2026-08-07_imaginary_valley.txt` |
| Imaginary Valley lab 2026-09-18 | lab | 2026-09-19 | `lab_2026-09-18_imaginary_valley.pdf` |
| Portal copy 2026-09-18 | lab | 2026-09-20 | `lab_2026-09-18_portal_copy.txt` |
| Baseline CT report | radiology | 2026-08-10 | `ct_2026-08-10_baseline.txt` |
| Follow-up CT report | radiology | 2026-10-12 | `ct_2026-10-12_followup.pdf` (2 pages) |

Each card then shows the file's SHA-256. Attaching the same file to another source of this case is refused as a
*Duplicate import* naming the first source.

## 3. Propose lab values
**Documents** → pick each lab document → **Propose CA-125 values**. Each adds one proposal to **Review** (nav badge shows
the count). No lab record exists yet.

Optional: attach `lab_2026-10_ambiguous_fax.txt` to a new source and propose values — you get three proposals that show
the parser reporting instead of guessing: `09/10/2026` has two readings (date left empty), `<5` is a bound (value left
empty), `result pending` has no value.

## 4. Transcribe imaging observations with anchors
**Documents → Baseline CT report**: select `Study date: 2026-08-10` with the mouse → **As imaging study** → the form opens
with the source and the cited passage; fill date `2026-08-10`, label → **Save record**. Select `measures 38 x 22 mm` →
**As lesion measurement** → study, key `reference-A`, location, 38 / 22 → save.

Repeat for **Follow-up CT report**: `Study date: 2026-10-12` (page 1) as a study; `measures 31 x 18 mm` (**page 2**) as a
measurement — and type the long axis **34** on purpose (the document says 31).

## 5. Review and accept
**Review** → each card shows the original line in context with the extracted passage highlighted, the extracted values,
editable values to record and any uncertainty. **Accept** each of the three. Accepted values become lab records with
provenance *extracted* and state *reviewed*. The page explains that this confirms the transcription only — not a diagnosis.

## 6. Timeline and chart
**Overview**: three CA-125 points (310 on 2026-08-07; 202 and 220 on 2026-09-18), each labelled with its stored value.
**Timeline**: every row shows provenance and a 📄 link; clicking it opens the document with the cited passage highlighted.
**Evidence** lists a *conflict*: CA-125 on 2026-09-18 is 202 U/mL in the clinic PDF and 220 U/mL in the portal copy. The
application keeps both and does not choose.

## 7. Compare measurements
**Imaging**: `reference-A` difference is **−4 mm** (34 − 38), because of the deliberate typo.

## 8. Correct the mistake
Click the measurement's 📄 link: the document opens at page 2 with `measures 31 x 18 mm` highlighted. Back in **Imaging →
Correct measurement #…**: long axis 31, reason “Mistyped; cited passage on page 2 reads 31 x 18 mm” → the difference
becomes **−7 mm**.

## 9. Audit
**Audit** shows the correction with ~~34~~ → 31 and the reason, and “Hash chain verified”. The record's **History** shows
the same.

## 10. Report and independent check
**Markdown report**: each observation line ends with its evidence, e.g.
`[transcribed; file `ct_2026-10-12_followup.pdf` sha256 …; page 2, chars 129-148: "measures 31 x 18 mm"]`. Save it and run:

```powershell
.\backend\.venv\Scripts\python.exe fixtures\audit_report.py report.md fixtures\fictional
```

The script does not use the app: it re-hashes each original file, re-extracts the page text and checks the quoted
passage at the cited range. Expected: `7/7 citations verified against original files`.

## 11. Back up and restore
Stop the API, then:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.backup "$env:TEMP\oncoscope-walkthrough-backups"
.\.venv\Scripts\python.exe -m app.restore <printed backup dir> --target "$env:TEMP\oncoscope-restored\oncoscope.sqlite3"
```

The restore prints `"consistent": true` with no differences: identical table hashes, valid audit chain, identical
document hashes. Start the API with `ONCOSCOPE_DB_PATH` pointing at the restored file and the case, report and audit
history are the same.
