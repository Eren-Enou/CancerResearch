"""Held-out benchmark documents, written AFTER parser ca125-lab v2 was frozen (commit 54161a5).

They are not used to tune the parser; their results are reported as they are. Run:
python fixtures/benchmark/make_holdout.py   then   python -m app.benchmark --holdout
All content is fictional.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_benchmark as mb  # noqa: E402  (shared writers and ground-truth helpers)

OUT = Path(__file__).resolve().parent / "holdout"
CASE = mb.CASE
HOLDOUT = []


def doc(id_, file, data, media_type, layout, patterns, observations, source_date=None, notes=""):
    HOLDOUT.append({"id": id_, "file": file, "data": data, "media_type": media_type, "category": "lab", "layout": layout,
                    "patterns": patterns, "text_layer": True, "source_date": source_date, "expected_observations": observations,
                    "expected_manual": [], "notes": notes})


doc("H01", "H01_tumour_marker_lowercase_unit.txt", mb.txt("FICTIONAL REGIONAL LAB", CASE, "Date collected: 2026-09-03", "",
    "Tumour marker CA 125 ........ 61.4 U/ml"), "text/plain", "text, lowercase unit spelling", ["unit-spelling"],
    [mb.obs(61.4, None, "2026-09-03", "61.4 U/ml", status="flagged", issues=["unit_not_accepted"],
            note="U/ml is a case variant; policy keeps it out until a person confirms")])
doc("H02", "H02_pipe_table_date_column.txt", mb.txt("FICTIONAL REGIONAL LAB", CASE, "", "| Analyte | Value | Unit | Collected  |",
    "|---------|-------|------|------------|", "| CA-125  | 75    | U/mL | 2026-09-03 |"), "text/plain", "pipe table, date column after unit",
    ["table", "row-dates"], [mb.obs(75, "U/mL", "2026-09-03", "| CA-125  | 75")])
doc("H03", "H03_sentence_result.txt", mb.txt("FICTIONAL REGIONAL LAB", CASE, "", "Result for CA-125 (serum): 98 U/mL, collected on 2026-09-12"),
    "text/plain", "single sentence with specimen note", ["specimen-note"], [mb.obs(98, "U/mL", "2026-09-12", "98 U/mL")])
doc("H04", "H04_two_specimens.txt", mb.txt("FICTIONAL REGIONAL LAB", CASE, "Specimen 1 - Collection date: 2026-09-01", "CA-125   140   U/mL",
    "Specimen 2 - Collection date: 2026-09-15", "CA-125   120   U/mL"), "text/plain", "two specimens with prefixed date labels",
    ["multiple-specimens"], [mb.obs(140, "U/mL", "2026-09-01", "140"), mb.obs(120, "U/mL", "2026-09-15", "120")])
doc("H05", "H05_continental_thousands.txt", mb.txt("FICTIONAL CONTINENTAL LAB", CASE, "Collected: 22.09.2026", "", "CA-125   1.234   U/mL"),
    "text/plain", "dotted thousands separator", ["thousands-separator"],
    [mb.obs(None, "U/mL", "2026-09-22", "1.234", status="flagged", issues=["value_separator"],
            note="in this fictional continental report 1.234 means one thousand two hundred thirty-four; the text alone cannot say")])
doc("H06", "H06_pdf_cells_out_of_order.pdf", mb.pdf([mb.lines(mb.BANNER, "FICTIONAL REGIONAL LAB", CASE, "Collection date: 2026-09-08")
    + [("text", 300, 640, 10, "U/mL"), ("text", 220, 640, 10, "88"), ("text", 50, 640, 10, "CA-125")]]), "application/pdf",
    "PDF row whose cells are drawn right-to-left", ["pdf", "content-order"], [mb.obs(88, "U/mL", "2026-09-08", "88")])
doc("H07", "H07_unit_label_colon.txt", mb.txt("FICTIONAL REGIONAL LAB", CASE, "Collection date: 2026-09-20", "", "CA-125 (U/mL): 64"),
    "text/plain", "unit in parentheses then colon", ["unit-before-value"], [mb.obs(64, "U/mL", "2026-09-20", "CA-125 (U/mL): 64")])
doc("H08", "H08_letter_with_value.txt", mb.txt("IMAGINARY VALLEY ONCOLOGY CLINIC (FICTIONAL)", "Letter date: 2026-08-25", "",
    "The fictional patient's CA-125 was 57 U/mL on 2026-08-20."), "text/plain", "narrative sentence with a value",
    ["narrative-value"], [mb.obs(57, "U/mL", "2026-08-20", "57 U/mL")], source_date="2026-08-25")


def main() -> None:
    (OUT / "documents").mkdir(parents=True, exist_ok=True)
    entries = []
    for d in HOLDOUT:
        (OUT / "documents" / d["file"]).write_bytes(d["data"])
        entries.append({**{k: v for k, v in d.items() if k != "data"}, "sha256": hashlib.sha256(d["data"]).hexdigest()})
    manifest = {"format": "oncoscope-benchmark-manifest", "version": 1, "case": "Fictional Case M26 (held-out)",
                "notice": "Held-out fictional documents written after parser v2 was frozen. Not used for tuning.",
                "documents": entries, "expected_findings": []}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(HOLDOUT)} held-out documents to {OUT}")


if __name__ == "__main__":
    main()
