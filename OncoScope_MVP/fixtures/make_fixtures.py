"""Generates the fictional source documents used by tests, the browser demonstration and the walkthrough.

Run: python fixtures/make_fixtures.py   (output is deterministic, so regenerated files are byte-identical)
Every document is invented. The names, clinic, values and dates do not describe any real person.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fictional"
BANNER = "FICTIONAL DOCUMENT - SYNTHETIC TEST DATA - NOT A REAL PATIENT"

TEXT_DOCUMENTS = {
    "lab_2026-08-07_imaginary_valley.txt": f"""{BANNER}
IMAGINARY VALLEY CLINIC LABORATORY
Patient: Fictional Case M25
Report date: 2026-08-08
Collection date: 2026-08-07

Test            Result   Unit
CA-125          310      U/mL
Hemoglobin      11.2     g/dL
""",
    # A second, independent document describing the same 2026-09-18 draw with a different value: a conflict to surface.
    "lab_2026-09-18_portal_copy.txt": f"""{BANNER}
FICTIONAL PATIENT PORTAL - RESULT SUMMARY
Patient: Fictional Case M25
Collection date: 18 Sep 2026

CA-125: 220 U/mL
""",
    # Deliberately incomplete: ambiguous date format, a qualified value, and a result without a value.
    "lab_2026-10_ambiguous_fax.txt": f"""{BANNER}
FICTIONAL FAXED RESULT (POOR COPY)
Patient: Fictional Case M25
Collection date: 09/10/2026

CA-125   188   U/mL
CA-125 (repeat)   <5   U/mL
CA 125   result pending
""",
    "ct_2026-08-10_baseline.txt": f"""{BANNER}
IMAGINARY VALLEY CLINIC RADIOLOGY
Study: Baseline abdominal CT (fictional)
Study date: 2026-08-10

Findings: Target lesion reference-A, fictional soft-tissue lesion, measures 38 x 22 mm.
Impression: Fictional educational report. Not diagnostic.
""",
}

PDF_DOCUMENTS = {
    "lab_2026-09-18_imaginary_valley.pdf": [[
        BANNER, "IMAGINARY VALLEY CLINIC LABORATORY", "Patient: Fictional Case M25",
        "Report date: 2026-09-19", "Collection date: 2026-09-18", "", "CA-125   202   U/mL",
    ]],
    "ct_2026-10-12_followup.pdf": [
        [BANNER, "IMAGINARY VALLEY CLINIC RADIOLOGY", "Study: Follow-up abdominal CT (fictional)", "Study date: 2026-10-12",
         "Technique: fictional; see page 2 for measurements."],
        [BANNER, "Findings: Target lesion reference-A, fictional soft-tissue lesion, measures 31 x 18 mm.",
         "Impression: Fictional educational report. Not a response classification."],
    ],
}


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    """Minimal PDF with a real text layer (Helvetica), one text line per row; no timestamps, so output is stable."""
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    font_id = 3 + 2 * len(pages)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()]
    for i, lines in enumerate(pages):
        ops = ["BT", "/F1 11 Tf", "14 TL", "40 760 Td"] + [f"({_escape(line)}) Tj T*" for line in lines] + ["ET"]
        stream = "\n".join(ops).encode("latin-1")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
                       f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def make_png(width: int = 240, height: int = 160) -> bytes:
    """A plain 'scanned note' placeholder image: pale page with a darker band where a fictional value would be."""
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            band = 60 <= y < 84 and 24 <= x < 216
            row += bytes((70, 80, 110) if band else (236, 232, 220))
        rows.append(bytes(row))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in TEXT_DOCUMENTS.items():
        (OUT / name).write_bytes(text.encode("utf-8"))
    for name, pages in PDF_DOCUMENTS.items():
        (OUT / name).write_bytes(make_pdf(pages))
    (OUT / "note_2026-09-25_scanned_placeholder.png").write_bytes(make_png())
    print(f"Wrote {len(TEXT_DOCUMENTS) + len(PDF_DOCUMENTS) + 1} fictional documents to {OUT}")


if __name__ == "__main__":
    main()
