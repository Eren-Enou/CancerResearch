# Local OCR — investigation only (M2.6)

**Status: not implemented, not tested, not a dependency.** Documents without a text layer (scans, photos, the benchmark's
`B20` PDF and `B21` image) are handled by the explicit manual workflow: render the page, mark a region by pointer or
keyboard, transcribe the value with that region as its evidence. The extraction endpoint refuses such documents with
"no text layer; transcribe it manually with a region anchor", and the benchmark counts them as `unsupported_identified`.

## Environment checked (2026-10-25)
No Tesseract binary on PATH or in `C:\Program Files\Tesseract-OCR`; `pytesseract`, `ocrmypdf` and Pillow are not installed.

## Options considered
| Option | Licence | Fit | Concerns |
|---|---|---|---|
| Tesseract 5 via `pytesseract` | Apache-2.0 | Local, mature, word boxes with confidences | Separate system install; accuracy on poor scans and tables is variable |
| OCRmyPDF (Tesseract + Ghostscript) | MPL-2.0 / AGPL (Ghostscript) | Adds a text layer to PDFs, keeps pages | Ghostscript licence and attack surface; heavier install |
| PaddleOCR / docTR | Apache-2.0 | Better layout handling | Large ML runtimes and model downloads; harder to make reproducible |

## Conditions before any OCR is added
1. Separate, optional module and install; the core pipeline and its tests must not change behaviour without it.
2. OCR text is a *derived* layer with its own extractor id (e.g. `tesseract-5.x-v1`) and per-word confidences; citations
   into it must never be presented as citations into an original text layer.
3. OCR output only ever produces review-queue proposals carrying an `ocr_low_confidence` issue below a threshold; nothing
   is auto-accepted.
4. A separate benchmark of scanned fictional documents with ground truth, reported apart from the text-layer benchmark.
5. Reproducibility: pinned engine and model versions recorded with each proposal.
