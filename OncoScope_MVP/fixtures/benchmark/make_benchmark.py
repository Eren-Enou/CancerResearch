"""Generates the M2.6 benchmark corpus and its ground-truth manifest.

Run: python fixtures/benchmark/make_benchmark.py      (deterministic: regenerated files are byte-identical)

Every document is fictional: invented clinics ("Northwind Fictional Labs", "Imaginary Valley"), an invented case
("Fictional Case M26"), invented values and dates. The ground truth below is written by hand from the document content
and is never derived from parser output. Field meanings are documented in fixtures/benchmark/README.md.
"""
from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent
DOCS = OUT / "documents"
BANNER = "FICTIONAL DOCUMENT - SYNTHETIC TEST DATA - NOT A REAL PATIENT"
CASE = "Patient: Fictional Case M26"


# --- PDF / PNG writers (no external libraries, no timestamps) ----------------------------------

def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf(pages: list[list[tuple]]) -> bytes:
    """pages: list of page item lists. Item: ('text', x, y, size, string) or ('rect', x, y, w, h, gray)."""
    n = len(pages)
    font_id = 3 + 2 * n
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{3 + 2 * i} 0 R' for i in range(n))}] /Count {n} >>".encode()]
    for i, items in enumerate(pages):
        ops = []
        for item in items:
            if item[0] == "text":
                _, x, y, size, text = item
                ops.append(f"BT /F1 {size} Tf 1 0 0 1 {x} {y} Tm ({_esc(text)}) Tj ET")
            else:
                _, x, y, w, h, gray = item
                ops.append(f"{gray} g {x} {y} {w} {h} re f")
        stream = "\n".join(ops).encode("latin-1")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
                    f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    out, offsets = b"%PDF-1.4\n", []
    for k, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % k + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def lines(*rows: str, x: int = 50, top: int = 740, step: int = 16, size: int = 11) -> list[tuple]:
    return [("text", x, top - i * step, size, row) for i, row in enumerate(rows) if row]


def table(rows: list[list[str]], columns: list[int], top: int, step: int = 18, size: int = 10) -> list[tuple]:
    """Each cell is drawn separately at its column position, as table-producing software does."""
    return [("text", columns[c], top - r * step, size, cell) for r, row in enumerate(rows) for c, cell in enumerate(row) if cell]


def png(width: int = 300, height: int = 200, bands: tuple = ((60, 84, 30, 270),)) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            dark = any(y0 <= y < y1 and x0 <= x < x1 for y0, y1, x0, x1 in bands)
            row += bytes((70, 80, 110) if dark else (236, 232, 220))
        rows.append(bytes(row))
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def txt(*rows: str) -> bytes:
    return ("\n".join((BANNER,) + rows) + "\n").encode("utf-8")


# --- Ground-truth helpers ------------------------------------------------------------------------

def obs(value, unit, date, evidence, page=1, status="complete", issues=(), qualifier=None, note=""):
    """One expected CA-125 observation as written in the document.

    status: complete   - every field is stated unambiguously; a correct extractor returns it with no issues
            flagged    - the document is ambiguous/incomplete/unsupported here; a correct extractor must raise one of
                         `issues` (fields that ARE stated must still be read correctly)
            no_text    - stated in the document but the file has no text layer: manual transcription required
    """
    return {"test": "CA-125", "value": value, "unit": unit, "date": date, "qualifier": qualifier, "page": page,
            "evidence": evidence, "status": status, "expected_issues": list(issues), "note": note}


def mention(evidence, page=1, issues=("no_result_on_line",), note=""):
    """CA-125 is mentioned without a result. Any proposal here must carry an issue; no observation is expected."""
    return {"test": "CA-125", "value": None, "unit": None, "date": None, "qualifier": None, "page": page, "evidence": evidence,
            "status": "mention", "expected_issues": list(issues), "note": note}


def measurement(lesion_key, long_mm, short_mm, page, evidence):
    return {"lesion_key": lesion_key, "long_axis_mm": long_mm, "short_axis_mm": short_mm, "page": page, "evidence": evidence}


DOCUMENTS = []


def doc(id_, file, data, media_type, category, layout, patterns, observations=(), manual=(), source_date=None,
        text_layer=True, notes=""):
    DOCUMENTS.append({"id": id_, "file": file, "data": data, "media_type": media_type, "category": category, "layout": layout,
                      "patterns": list(patterns), "text_layer": text_layer, "source_date": source_date,
                      "expected_observations": list(observations), "expected_manual": list(manual), "notes": notes})


# --- Laboratory documents --------------------------------------------------------------------------

doc("B01", "B01_lab_keyvalue.txt", txt("NORTHWIND FICTIONAL LABS", CASE, "Collection date: 2026-08-07", "Reported: 2026-08-08",
    "", "CA-125: 310 U/mL"), "text/plain", "lab", "text key-value", ["iso-date", "collection-vs-report-date"],
    [obs(310, "U/mL", "2026-08-07", "CA-125: 310 U/mL")], source_date="2026-08-08")

doc("B02", "B02_lab_pipe_table.txt", txt("NORTHWIND FICTIONAL LABS", CASE, "Collected: 2026-08-28 07:45", "",
    "| Test   | Result | Units | Reference |", "|--------|--------|-------|-----------|",
    "| CA-125 | 288    | U/mL  | 0-35      |", "| CEA    | 2.1    | ng/mL | 0-5       |"), "text/plain", "lab", "markdown pipe table",
    ["table", "time-suffix", "reference-range", "other-analytes"], [obs(288, "U/mL", "2026-08-28", "| CA-125 | 288    | U/mL")],
    source_date="2026-08-29")

doc("B03", "B03_lab_flag_column.txt", txt("IMAGINARY VALLEY CLINIC LABORATORY", CASE, "Specimen collected: 18 Sep 2026",
    "Report date: 19 Sep 2026", "", "TEST                 RESULT  FLAG  UNITS   REF RANGE",
    "Cancer antigen 125   202     H     U/mL    <35"), "text/plain", "lab", "fixed-width table with flag column",
    ["label-variant", "named-month-date", "flag-column", "reference-range"],
    [obs(202, "U/mL", "2026-09-18", "Cancer antigen 125   202     H     U/mL")], source_date="2026-09-19")

cumulative_rows = [["Date", "Test", "Result", "Units"], ["2026-08-07", "CA-125", "310", "U/mL"], ["2026-08-28", "CA-125", "288", "U/mL"],
                   ["2026-09-18", "CA-125", "202", "U/mL"], ["2026-10-09", "CA-125", "174", "U/mL"]]
doc("B04", "B04_lab_cumulative.pdf", pdf([lines(BANNER, "NORTHWIND FICTIONAL LABS - CUMULATIVE REPORT", CASE, "Report date: 2026-10-10")
    + table(cumulative_rows, [50, 150, 250, 330], top=660)]), "application/pdf", "lab", "PDF cumulative table (cells drawn separately)",
    ["pdf", "table", "row-dates", "repeated-measurements"],
    [obs(310, "U/mL", "2026-08-07", "310"), obs(288, "U/mL", "2026-08-28", "288"), obs(202, "U/mL", "2026-09-18", "202"),
     obs(174, "U/mL", "2026-10-09", "174")], source_date="2026-10-10")

doc("B05", "B05_lab_multipage.pdf", pdf([
    lines(BANNER, "NORTHWIND FICTIONAL LABS", CASE, "Collection date: 2026-10-09", "Report date: 2026-10-10", "Results are listed on page 2."),
    lines(BANNER, "RESULTS") + table([["Test", "Result", "Units"], ["CA-125", "174", "U/mL"]], [50, 200, 280], top=690)]),
    "application/pdf", "lab", "PDF multi-page (date on page 1, result on page 2)", ["pdf", "multi-page", "document-level-date"],
    [obs(174, "U/mL", "2026-10-09", "174", page=2)], source_date="2026-10-10")

doc("B06", "B06_lab_two_column.pdf", pdf([lines(BANNER) + lines("NORTHWIND FICTIONAL LABS", CASE, "Collected: 2026-10-14", top=700)
    + lines("Results", "CA-125   155   U/mL", "Hemoglobin   11.9   g/dL", x=330, top=700)]), "application/pdf", "lab",
    "PDF two-column layout", ["pdf", "two-column"], [obs(155, "U/mL", "2026-10-14", "155")], source_date="2026-10-15")

doc("B07", "B07_lab_ambiguous_date.txt", txt("FICTIONAL FAXED RESULT", CASE, "Collection date: 03/04/2026", "", "CA-125   410   U/mL"),
    "text/plain", "lab", "text, numeric date", ["ambiguous-date"],
    [obs(410, "U/mL", None, "CA-125   410   U/mL", status="flagged", issues=["date_ambiguous"],
         note="03/04/2026 can be 3 April or 4 March; the document does not say")])

doc("B08", "B08_lab_missing_unit.txt", txt("NORTHWIND FICTIONAL LABS", CASE, "Collected: 2026-09-01", "", "CA-125 .......... 95"),
    "text/plain", "lab", "text, dotted leader, no unit", ["missing-unit"],
    [obs(95, None, "2026-09-01", "CA-125 .......... 95", status="flagged", issues=["unit_missing"])], source_date="2026-09-02")

doc("B09", "B09_lab_less_than.txt", txt("NORTHWIND FICTIONAL LABS", CASE, "Collected 2026-10-20", "", "CA-125   <5   U/mL"),
    "text/plain", "lab", "text, label without colon", ["qualified-value", "label-without-colon"],
    [obs(None, "U/mL", "2026-10-20", "CA-125   <5   U/mL", status="flagged", issues=["value_qualifier"], qualifier="<5")],
    source_date="2026-10-21")

doc("B10", "B10_lab_greater_than.txt", txt("IMAGINARY VALLEY CLINIC LABORATORY", CASE, "Collection date: 2026-08-01", "",
    "CA-125 result: >1000 U/mL (above reportable range)"), "text/plain", "lab", "text, result label", ["qualified-value"],
    [obs(None, "U/mL", "2026-08-01", "CA-125 result: >1000 U/mL", status="flagged", issues=["value_qualifier"], qualifier=">1000")],
    source_date="2026-08-02")

doc("B11", "B11_lab_unit_in_header.txt", txt("NORTHWIND FICTIONAL LABS", CASE, "Collection date: 2026-08-14", "",
    "CA-125 (kU/L)   300"), "text/plain", "lab", "text, unit in parentheses before value", ["unit-before-value", "alternate-unit"],
    [obs(300, "kU/L", "2026-08-14", "CA-125 (kU/L)   300")], source_date="2026-08-15")

doc("B12", "B12_lab_unsupported_unit.txt", txt("FICTIONAL OUTSIDE LAB", CASE, "Collection date: 2026-09-10", "", "CA125 = 45 IU/mL"),
    "text/plain", "lab", "text, equals sign, unaccepted unit", ["unsupported-unit", "label-variant"],
    [obs(45, None, "2026-09-10", "CA125 = 45 IU/mL", status="flagged", issues=["unit_not_accepted"],
         note="IU/mL is not an accepted CA-125 spelling; it is reported, never converted")])

doc("B13", "B13_lab_value_next_line.txt", txt("NORTHWIND FICTIONAL LABS", CASE, "Collection date: 2026-07-30", "",
    "Analyte: CA-125", "Result: 512 U/mL"), "text/plain", "lab", "text, label and value on separate lines", ["value-next-line"],
    [obs(512, "U/mL", "2026-07-30", "Result: 512 U/mL", status="flagged", issues=["value_on_next_line"],
         note="value is on the line after the test name; read but flagged for confirmation")])

doc("B14", "B14_lab_amended.txt", txt("AMENDED REPORT - IMAGINARY VALLEY CLINIC LABORATORY", CASE, "Collected: 2026-09-18", "",
    "CA-125: 202 U/mL (previously reported as 220 U/mL)"), "text/plain", "lab", "text, amended result with prior value",
    ["multiple-values", "amended-report"],
    [obs(202, "U/mL", "2026-09-18", "CA-125: 202 U/mL (previously reported as 220 U/mL)", status="flagged", issues=["multiple_values"])])

doc("B15", "B15_lab_decimal_comma.txt", txt("FICTIONAL CONTINENTAL LAB", CASE, "Collection date: 2026-10-01", "", "CA-125   18,5   kU/L"),
    "text/plain", "lab", "text, decimal comma", ["decimal-comma"],
    [obs(None, "kU/L", "2026-10-01", "CA-125   18,5   kU/L", status="flagged", issues=["value_separator"])])

doc("B16", "B16_portal_conflicting.txt", txt("FICTIONAL PATIENT PORTAL - RESULT SUMMARY", CASE, "Collection date: 18 Sep 2026", "",
    "CA-125: 220 U/mL"), "text/plain", "lab", "text portal summary", ["conflicting-value"],
    [obs(220, "U/mL", "2026-09-18", "CA-125: 220 U/mL", note="conflicts with B03/B04 (202 U/mL) for the same draw")],
    source_date="2026-09-20")

doc("B17", "B17_clinic_letter.txt", txt("IMAGINARY VALLEY ONCOLOGY CLINIC (FICTIONAL)", "Letter date: 2026-10-16", "",
    "Dear colleague,", "The CA-125 values were discussed with the fictional patient.", "Plan: repeat CA-125 in three weeks."),
    "text/plain", "letter", "narrative letter", ["narrative-mention"],
    [mention("The CA-125 values were discussed"), mention("Plan: repeat CA-125 in three weeks.")], source_date="2026-10-16")

doc("B18", "B18_lab_report_date_only.txt", txt("NORTHWIND FICTIONAL LABS", CASE, "Report date: 2026-09-05", "", "CA-125   133   U/mL"),
    "text/plain", "lab", "text, report date but no collection date", ["missing-collection-date"],
    [obs(133, "U/mL", None, "CA-125   133   U/mL", status="flagged", issues=["date_missing"],
         note="only a report date is printed; it is not used as the collection date")])

doc("B19", "B19_lab_dotted_date.txt", txt("FICTIONAL CONTINENTAL LAB", CASE, "Collected: 25.09.2026", "", "CA-125   190   U/mL"),
    "text/plain", "lab", "text, day.month.year date", ["dotted-date"], [obs(190, "U/mL", "2026-09-25", "CA-125   190   U/mL",
    note="25.09.2026 has one valid reading")], source_date="2026-09-26")

doc("B20", "B20_lab_scanned_no_text.pdf", pdf([[("rect", 60, 700, 480, 14, 0.2), ("rect", 60, 660, 300, 10, 0.4),
    ("rect", 60, 600, 420, 12, 0.3), ("rect", 60, 560, 200, 10, 0.4)]]), "application/pdf", "lab", "PDF without text layer (scan stand-in)",
    ["no-text-layer"], [obs(260, "U/mL", "2026-08-21", None, status="no_text",
    note="the dark band at roughly 10,22,78,3 % stands for the result line; manual region transcription required")],
    text_layer=False)

doc("B21", "B21_note_scan.png", png(), "image/png", "note", "image (scan stand-in)", ["image", "no-text-layer"],
    [obs(240, "U/mL", "2026-09-25", None, status="no_text", note="the band at 10,30,80,12 % stands for a handwritten result")],
    text_layer=False)

doc("B22", "B22_portal_duplicate.txt", txt("FICTIONAL PATIENT PORTAL - RESULT SUMMARY", CASE, "Collection date: 2026-08-07", "",
    "CA-125: 310 U/mL"), "text/plain", "lab", "text portal summary", ["duplicate-report"],
    [obs(310, "U/mL", "2026-08-07", "CA-125: 310 U/mL", note="same draw as B01 and the B04 row")], source_date="2026-08-09")

# --- Radiology documents (no automated extraction; ground truth for manual transcription) ------------

doc("R01", "R01_ct_baseline.txt", txt("IMAGINARY VALLEY CLINIC RADIOLOGY", "Study: Baseline abdominal CT (fictional)", "Study date: 2026-08-10",
    "Clinical history: elevated CA-125.", "Findings: Target lesion reference-A, fictional soft-tissue lesion, measures 38 x 22 mm.",
    "Impression: Fictional educational report. Not diagnostic."), "text/plain", "radiology", "text radiology report",
    ["radiology", "narrative-mention"], [mention("Clinical history: elevated CA-125.")],
    manual=[measurement("reference-A", 38, 22, 1, "measures 38 x 22 mm")], source_date="2026-08-10")

doc("R02", "R02_ct_followup.pdf", pdf([
    lines(BANNER, "IMAGINARY VALLEY CLINIC RADIOLOGY", "Study: Follow-up abdominal CT (fictional)", "Study date: 2026-10-12",
          "Technique: fictional; measurements on page 2."),
    lines(BANNER, "Findings: Target lesion reference-A measures 31 x 18 mm.", "A new fictional nodule reference-B measures 9 mm.",
          "Impression: Fictional educational report. Not a response classification.")]), "application/pdf", "radiology",
    "PDF radiology report, 2 pages", ["radiology", "pdf", "multi-page"],
    manual=[measurement("reference-A", 31, 18, 2, "reference-A measures 31 x 18 mm"), measurement("reference-B", 9, None, 2, "reference-B measures 9 mm")],
    source_date="2026-10-12")

doc("R03", "R03_mri_narrative.txt", txt("IMAGINARY VALLEY CLINIC RADIOLOGY", "Study: MRI pelvis (fictional)", "Study date: 2026-09-02",
    "Findings: No measurable lesion is described in this fictional report.", "Impression: Fictional. Not diagnostic."),
    "text/plain", "radiology", "text radiology narrative", ["radiology", "no-measurements"], source_date="2026-09-02")

doc("R04", "R04_us_report.pdf", pdf([lines(BANNER, "IMAGINARY VALLEY CLINIC ULTRASOUND", "Study: Abdominal ultrasound (fictional)",
    "Study date: 2026-09-30", "Findings: Fictional free fluid noted; no measurements recorded.")]), "application/pdf", "radiology",
    "PDF ultrasound report", ["radiology", "pdf", "no-measurements"], source_date="2026-09-30")

EXPECTED_FINDINGS = [
    {"kind": "conflicting_values", "date": "2026-09-18", "documents": ["B03", "B04", "B16"],
     "note": "202 U/mL (B03, B04) vs 220 U/mL (B16); the application must keep both"},
    {"kind": "possible_duplicate", "date": "2026-08-07", "documents": ["B01", "B04", "B22"]},
    {"kind": "possible_duplicate", "date": "2026-08-28", "documents": ["B02", "B04"]},
    {"kind": "possible_duplicate", "date": "2026-10-09", "documents": ["B04", "B05"]},
]


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    manifest_docs = []
    for d in DOCUMENTS:
        (DOCS / d["file"]).write_bytes(d["data"])
        entry = {k: v for k, v in d.items() if k != "data"}
        entry["sha256"] = hashlib.sha256(d["data"]).hexdigest()
        manifest_docs.append(entry)
    manifest = {"format": "oncoscope-benchmark-manifest", "version": 1, "case": "Fictional Case M26",
                "notice": "Entirely fictional documents and ground truth for software testing. Not real patient data.",
                "documents": manifest_docs, "expected_findings": EXPECTED_FINDINGS}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(DOCUMENTS)} fictional documents and manifest.json to {OUT}")


if __name__ == "__main__":
    main()
